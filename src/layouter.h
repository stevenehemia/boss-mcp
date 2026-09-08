#pragma once
#include <cstddef>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>
#include "expression.h"


enum class TaskHint { Unknown, Lookup, Extremum, Aggregate };

TaskHint parseTaskHint(const std::string& value);

struct CostConfig {
  // Character budget for one tool result
  // 0 means no budget declared: nothing is paginated, everything is served whole
  size_t budgetChars = 0;

  std::vector<ResultFormat> candidates = {
      ResultFormat::ColumnarJson,
      ResultFormat::PositionalRowsJson,
      ResultFormat::IndexedColumnarJson,
      ResultFormat::ArrayOfObjectsJson,
  };

  // Linear regression fit chars/token per format
  std::unordered_map<ResultFormat, double> charsPerToken = {
      {ResultFormat::ColumnarJson, 1.87},
      {ResultFormat::IndexedColumnarJson, 2.13},
      {ResultFormat::PositionalRowsJson, 1.88},
      {ResultFormat::ArrayOfObjectsJson, 2.95},
  };

  // Whether the calling agent is using extended thinking
  bool defaultThinking = false;

  // Measured error rate per task bucket x thinking mode x format
  // 0 for aggregate the McNemar comparison there non-significant
  // Unknown is minimax over the three measured buckets, pick the
  // safest format across tasks
  struct AccuracyBucket {
    std::unordered_map<ResultFormat, double> errorOff;
    std::unordered_map<ResultFormat, double> errorOn;
  };
  
  std::unordered_map<TaskHint, AccuracyBucket> accuracyTable = {
      {TaskHint::Lookup, {
          {{ResultFormat::ColumnarJson, 0.40},       {ResultFormat::IndexedColumnarJson, 0.08},
           {ResultFormat::PositionalRowsJson, 0.00}, {ResultFormat::ArrayOfObjectsJson, 0.00}},
          {{ResultFormat::ColumnarJson, 0.02},       {ResultFormat::IndexedColumnarJson, 0.00},
           {ResultFormat::PositionalRowsJson, 0.00}, {ResultFormat::ArrayOfObjectsJson, 0.00}},
      }},
      {TaskHint::Extremum, {
          {{ResultFormat::ColumnarJson, 0.65},       {ResultFormat::IndexedColumnarJson, 0.28},
           {ResultFormat::PositionalRowsJson, 0.55}, {ResultFormat::ArrayOfObjectsJson, 0.43}},
          {{ResultFormat::ColumnarJson, 0.18},       {ResultFormat::IndexedColumnarJson, 0.08},
           {ResultFormat::PositionalRowsJson, 0.38}, {ResultFormat::ArrayOfObjectsJson, 0.22}},
      }},
      {TaskHint::Aggregate, {
          {{ResultFormat::ColumnarJson, 0.00},       {ResultFormat::IndexedColumnarJson, 0.00},
           {ResultFormat::PositionalRowsJson, 0.00}, {ResultFormat::ArrayOfObjectsJson, 0.00}},
          {{ResultFormat::ColumnarJson, 0.00},       {ResultFormat::IndexedColumnarJson, 0.00},
           {ResultFormat::PositionalRowsJson, 0.00}, {ResultFormat::ArrayOfObjectsJson, 0.00}},
      }},
      {TaskHint::Unknown, {
          {{ResultFormat::ColumnarJson, 0.65},       {ResultFormat::IndexedColumnarJson, 0.25},
           {ResultFormat::PositionalRowsJson, 0.75}, {ResultFormat::ArrayOfObjectsJson, 0.45}},
          {{ResultFormat::ColumnarJson, 0.15},       {ResultFormat::IndexedColumnarJson, 0.10},
           {ResultFormat::PositionalRowsJson, 0.40}, {ResultFormat::ArrayOfObjectsJson, 0.20}},
      }},
  };
};

// tokenCost = renderedChars / charsPerToken if (renderedChars < budgetChars)
// Otherwise, tokenCost = sums (N+1-k)*page_k / charsPerToken
// since a loop resends the whole history every call
double tokenCost(ResultFormat format, size_t renderedChars, size_t nrows, size_t budgetChars,
                  const CostConfig& config);

// Find the format with the lowest error rate for the given task and thinking mode.
// Ties are broken by config.candidates' iteration order
// (sorted by least token usage first)
ResultFormat accuracyBest(TaskHint task, bool thinking, const CostConfig& config);

// Checks the for the completeness of calibrated tables.
// Returns the first violation found, or nullopt.
std::optional<std::string> validateCostConfig(const CostConfig& config);

// How the result is being delivered relative to the configured budget
enum class Delivery {
  Whole, // The whole result fits within the budget
  Paged, // The result is paginated because it exceeds the bdget
  Oversized, // Not even a single row fits within the budget
};

struct LayoutDecision {
  ResultFormat format = ResultFormat::ColumnarJson;
  std::string text;
  Delivery delivery = Delivery::Whole;
};

// labelOffset enables IndexedColumnarJson's row labels to continue
// with offset, offset+1, ... for page 2
// decisionBasis passes the re-evaluated pre-Slice table here and
// the format is chosen the SAME way page 1 chose it, preventing
// mid-retrieval format change
LayoutDecision chooseLayout(const TableView& table, const CostConfig& config, TaskHint task,
                            size_t labelOffset = 0, const TableView* decisionBasis = nullptr);
