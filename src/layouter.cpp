#include <algorithm>
#include <string>
#include "layouter.h"
#include "strutil.h"

using json = nlohmann::json;

namespace {

// The pagination envelope: the truncated table plus one count of what was
// withheld from THIS call's evaluation
json pageEnvelope(const TableView& table, ResultFormat format, size_t count, size_t labelOffset) {
  json envelope;
  envelope["table"] = serializeTable(table, format, 0, count, labelOffset);
  envelope["overbudget_row_count"] = table.nrows - count;
  return envelope;
}

// The largest leading page whose envelope fits budgetChars:
struct FittedPage {
  size_t rows = 0;
  std::string text;
};

FittedPage maxRowsFittingEnvelope(const TableView& table, ResultFormat format, size_t budgetChars,
                                  size_t labelOffset) {
  FittedPage fitted;
  const auto envelopeSize = [&](size_t count) {
    std::string text = pageEnvelope(table, format, count, labelOffset).dump();
    const size_t size = text.size();
    if(size <= budgetChars) fitted = {count, std::move(text)};
    return size;
  };

  if(envelopeSize(0) > budgetChars) return {};

  size_t lo = 0, hi = table.nrows;
  while(lo < hi) {
    const size_t mid = lo + (hi - lo + 1) / 2;
    if(envelopeSize(mid) <= budgetChars) {
      lo = mid;
    } else {
      hi = mid - 1;
    }
  }

  if(lo == 0) return {};
  return fitted;
}

// The calibrated error-rate row for a task and thinking mode
const std::unordered_map<ResultFormat, double>& errorRates(const CostConfig& config, TaskHint task,
                                                             bool thinking) {
  const CostConfig::AccuracyBucket& bucket = config.accuracyTable.at(task);
  return thinking ? bucket.errorOn : bucket.errorOff;
}

}  // namespace


TaskHint parseTaskHint(const std::string& value) {
  const std::string v = toLower(value);
  if(v == "lookup") return TaskHint::Lookup;
  if(v == "extremum") return TaskHint::Extremum;
  if(v == "aggregate") return TaskHint::Aggregate;
  return TaskHint::Unknown;
}


double tokenCost(ResultFormat format, size_t renderedChars, size_t nrows, size_t budgetChars,
                  const CostConfig& config) {
  const double perToken = config.charsPerToken.at(format);

  // Fits whole or nothing to reason about
  if(budgetChars == 0 || renderedChars <= budgetChars || nrows == 0) {
    return static_cast<double>(renderedChars) / perToken;
  }

  // Pagination required. Page boundaries are estimated from average chars per row
  // since the cost scale linearly enough in row count
  const double avgRowChars = static_cast<double>(renderedChars) / static_cast<double>(nrows);
  // Constant estimate of pageEnvelope's own wrapper ({"table", "overbudget_row_count"})
  constexpr size_t envelopeOverhead = 40;
  const size_t usableBudget = budgetChars > envelopeOverhead ? budgetChars - envelopeOverhead : 1;
  size_t rowsPerPage = static_cast<size_t>(usableBudget / avgRowChars);
  // Even one row already exceeds the budget,
  // still page singly rather than divide by zero below
  if(rowsPerPage == 0) rowsPerPage = 1;

  const size_t pageCount = (nrows + rowsPerPage - 1) / rowsPerPage;
  double weightedChars = 0.0;
  size_t rowsLeft = nrows;
  for(size_t k = 1; k <= pageCount; ++k) {
    const size_t pageRows = std::min(rowsPerPage, rowsLeft);
    const double pageChars = static_cast<double>(pageRows) * avgRowChars + envelopeOverhead;
    // Page k is resent on every later call
    const double weight = static_cast<double>(pageCount + 1 - k);
    weightedChars += weight * pageChars;
    rowsLeft -= pageRows;
  }
  return weightedChars / perToken;
}


ResultFormat accuracyBest(TaskHint task, bool thinking, const CostConfig& config) {
  const auto& errors = errorRates(config, task, thinking);
  ResultFormat best = ResultFormat::ColumnarJson;
  double bestError = 1.0;
  bool haveBest = false;
  // Tie break to candidate with least token usage first,
  // already sorted by config.candidates' order
  for(ResultFormat format : config.candidates) {
    const double error = errors.at(format);
    if(!haveBest || error < bestError) {
      bestError = error;
      best = format;
      haveBest = true;
    }
  }
  return best;
}


LayoutDecision chooseLayout(const TableView& table, const CostConfig& config, TaskHint task,
                            size_t labelOffset, const TableView* decisionBasis) {
  // When set, selection is costed agianst this table instead of the one being served,
  // so the decision matches with page 1 on a follow-up Slice as smaller remaining
  // row counts can skew the decision
  const TableView& costTable = decisionBasis ? *decisionBasis : table;

  // The rendered text is kept, not just its size, so the common case
  // (decisionBasis unset, costTable is table) reuses the winner's bytes below
  // instead of serialising them twice.
  struct Rendered {
    ResultFormat format;
    std::string text;
  };
  std::vector<Rendered> rendered;
  rendered.reserve(config.candidates.size());
  for(ResultFormat format : config.candidates) {
    rendered.push_back(
        {format, serializeTable(costTable, format, 0, costTable.nrows, labelOffset).dump()});
  }

  // accuracy_penalty: cost of the most reliable format multiplied by
  // the gap between the candidate's error and the best format's error.
  const auto& errors = errorRates(config, task, config.defaultThinking);
  const ResultFormat best = accuracyBest(task, config.defaultThinking, config);
  const double bestError = errors.at(best);
  const auto bestIt = std::find_if(rendered.begin(), rendered.end(),
                                   [best](const Rendered& r) { return r.format == best; });
  const double bestCost =
      tokenCost(best, bestIt->text.size(), costTable.nrows, config.budgetChars, config);

  // argmin over token_cost + accuracy_penalty, costed against costTable.
  size_t winnerIndex = 0;
  double winnerCost = 0.0;
  bool haveWinner = false;
  for(size_t i = 0; i < rendered.size(); ++i) {
    const ResultFormat format = rendered[i].format;
    const double cost =
        tokenCost(format, rendered[i].text.size(), costTable.nrows, config.budgetChars, config) +
        bestCost * (errors.at(format) - bestError);
    if(!haveWinner || cost < winnerCost) {
      winnerIndex = i;
      winnerCost = cost;
      haveWinner = true;
    }
  }

  const ResultFormat winnerFormat = rendered[winnerIndex].format;
  // Unset decisionBasis reuses the render above; set forces the one
  // unavoidable second serialisation.
  std::string winnerText = (decisionBasis == nullptr)
      ? std::move(rendered[winnerIndex].text)
      : serializeTable(table, winnerFormat, 0, table.nrows, labelOffset).dump();
  const size_t winnerSize = winnerText.size();

  if(config.budgetChars == 0 || winnerSize <= config.budgetChars) {
    return {winnerFormat, std::move(winnerText), Delivery::Whole};
  }

  // Doesn't fit whole, so paginate
  FittedPage page = maxRowsFittingEnvelope(table, winnerFormat, config.budgetChars, labelOffset);
  if(page.rows == 0) {
    // Not even one row fits, so serve whole and flag it.
    return {winnerFormat, std::move(winnerText), Delivery::Oversized};
  }

  return {winnerFormat, std::move(page.text), Delivery::Paged};
}


std::optional<std::string> validateCostConfig(const CostConfig& config) {
  if(config.candidates.empty()) return std::string("candidates is empty");

  // parseTaskHint can yield any of these, so each needs a bucket.
  struct TaskName {
    TaskHint task;
    const char* name;
  };
  static constexpr TaskName tasks[] = {{TaskHint::Unknown, "Unknown"},
                                       {TaskHint::Lookup, "Lookup"},
                                       {TaskHint::Extremum, "Extremum"},
                                       {TaskHint::Aggregate, "Aggregate"}};
  for(const TaskName& t : tasks) {
    if(!config.accuracyTable.count(t.task)) {
      return std::string("accuracyTable has no ") + t.name + " bucket";
    }
  }

  for(size_t i = 0; i < config.candidates.size(); ++i) {
    const ResultFormat format = config.candidates[i];
    const std::string who = "candidates[" + std::to_string(i) + "]";
    if(!config.charsPerToken.count(format)) return who + " has no charsPerToken entry";
    for(const TaskName& t : tasks) {
      const CostConfig::AccuracyBucket& bucket = config.accuracyTable.at(t.task);
      if(!bucket.errorOff.count(format) || !bucket.errorOn.count(format)) {
        return who + " is missing from the " + t.name + " accuracy bucket";
      }
    }
  }
  return std::nullopt;
}
