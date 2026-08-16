#include <algorithm>
#include <string>
#include "layouter.h"
#include "strutil.h"

using json = nlohmann::json;

namespace {

// The pagination envelope: the truncated table plus one count of what was
// withheld from THIS call's evaluation
json pageEnvelope(const ColumnData& table, ResultFormat format, size_t count, size_t labelOffset) {
  json envelope;
  envelope["table"] = serializeTable(table, format, 0, count, labelOffset);
  envelope["overbudget_row_count"] = table.nrows - count;
  return envelope;
}

// Return the largest leading row count whose envelope fits budgetChars
// or 0 if not even one row does
size_t maxRowsFittingEnvelope(const ColumnData& table, ResultFormat format, size_t budgetChars,
                              size_t labelOffset) {
  const auto envelopeSize = [&](size_t count) {
    return pageEnvelope(table, format, count, labelOffset).dump().size();
  };

  if(envelopeSize(0) > budgetChars) return 0;

  size_t lo = 0, hi = table.nrows;
  while(lo < hi) {
    const size_t mid = lo + (hi - lo + 1) / 2;
    if(envelopeSize(mid) <= budgetChars) {
      lo = mid;
    } else {
      hi = mid - 1;
    }
  }
  return lo;
}

// The calibrated bucket for a task, falling back to Unknown's if a
// deployer-overridden accuracyTable dropped the task entirely.
const CostConfig::AccuracyBucket& accuracyBucket(TaskHint task, const CostConfig& config) {
  static const CostConfig::AccuracyBucket empty{};
  const auto it = config.accuracyTable.find(task);
  if(it != config.accuracyTable.end()) return it->second;
  const auto fallback = config.accuracyTable.find(TaskHint::Unknown);
  return fallback != config.accuracyTable.end() ? fallback->second : empty;
}

const std::unordered_map<ResultFormat, double>& errorRates(const CostConfig::AccuracyBucket& bucket,
                                                             bool thinking) {
  return thinking ? bucket.errorOn : bucket.errorOff;
}

double errorRate(const std::unordered_map<ResultFormat, double>& errors, ResultFormat format) {
  const auto it = errors.find(format);
  return it != errors.end() ? it->second : 1.0;
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
  const auto it = config.charsPerToken.find(format);
  // 2.0 is a middle of the calibrated range (1.731-2.969)
  const double perToken = (it != config.charsPerToken.end()) ? it->second : 2.0;

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
  const auto& errors = errorRates(accuracyBucket(task, config), thinking);
  ResultFormat best = ResultFormat::ColumnarJson;
  double bestError = 1.0;
  bool haveBest = false;
  // Tie break to candidate with least token usage first,
  // already sorted by config.candidates' order
  for(ResultFormat format : config.candidates) {
    const double error = errorRate(errors, format);
    if(!haveBest || error < bestError) {
      bestError = error;
      best = format;
      haveBest = true;
    }
  }
  return best;
}


double accuracyPenalty(ResultFormat format, TaskHint task, bool thinking,
                        const std::unordered_map<ResultFormat, size_t>& renderedChars,
                        size_t nrows, size_t budgetChars,
                        const CostConfig& config) {
  const auto& errors = errorRates(accuracyBucket(task, config), thinking);
  const ResultFormat best = accuracyBest(task, thinking, config);
  const double gap = errorRate(errors, format) - errorRate(errors, best);

  const auto it = renderedChars.find(best);
  const size_t bestSize = (it != renderedChars.end()) ? it->second : 0;

  return tokenCost(best, bestSize, nrows, budgetChars, config) * gap;
}


LayoutDecision chooseLayout(const ColumnData& table, const CostConfig& config, TaskHint task,
                            size_t labelOffset, const ColumnData* decisionBasis) {
  // When set, selection is costed agianst this table instead of the one being served,
  // so the decision matches with page 1 on a follow-up Slice as smaller remaining
  // row counts can skew the decision
  const ColumnData& costTable = decisionBasis ? *decisionBasis : table;

  // The rendered text is kept, not just its size, so the common case
  // (decisionBasis unset, costTable IS table) reuses the winner's bytes below
  // instead of serialising them twice.
  struct Rendered {
    ResultFormat format;
    std::string text;
  };
  std::vector<Rendered> rendered;
  std::unordered_map<ResultFormat, size_t> sizes;
  rendered.reserve(config.candidates.size());
  for(ResultFormat format : config.candidates) {
    std::string text = serializeTable(costTable, format, 0, costTable.nrows, labelOffset).dump();
    sizes[format] = text.size();
    rendered.push_back({format, std::move(text)});
  }

  // if config.candidates is empty, fall back to columnar
  if(rendered.empty()) {
    return {ResultFormat::ColumnarJson,
            serializeTable(table, ResultFormat::ColumnarJson, 0, table.nrows, labelOffset).dump(),
            Delivery::Whole};
  }

  // argmin over token_cost + accuracy_penalty, costed against costTable
  size_t winnerIndex = 0;
  double winnerCost = 0.0;
  bool haveWinner = false;
  for(size_t i = 0; i < rendered.size(); ++i) {
    const ResultFormat format = rendered[i].format;
    const double cost = tokenCost(format, sizes[format], costTable.nrows, config.budgetChars, config) +
                         accuracyPenalty(format, task, config.defaultThinking, sizes,
                                         costTable.nrows, config.budgetChars, config);
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
  const size_t rowsFit = maxRowsFittingEnvelope(table, winnerFormat, config.budgetChars, labelOffset);
  if(rowsFit == 0) {
    // Not even one row fits, so serve whole and flag it.
    return {winnerFormat, std::move(winnerText), Delivery::Oversized};
  }

  return {winnerFormat, pageEnvelope(table, winnerFormat, rowsFit, labelOffset).dump(),
          Delivery::Paged};
}
