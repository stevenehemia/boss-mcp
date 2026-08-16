#pragma once
#include <cstddef>
#include <optional>
#include <string>
#include <vector>
#include "BOSS.h"
#include "boss_raii.h"
#include "nlohmann/json.hpp"

// ArrayJson  : positional arrays  ["Op", arg, ...] / atoms ["String","s"]
// ObjectJson : tagged objects     {"type":"call","head":"Op","args":[...]}
enum class QueryFormat { ArrayJson, ObjectJson };

// ColumnarJson        : column-major, no type tags
//                       ["col", v1, v2, ...], ...
// TypedColumnarJson   : BOSS-native expression
//                       ["Table", ["col", ["Type", v1], ...], ...]
// IndexedColumnarJson : column-major, each cell paired with its row index
//                       ["col", [0, v1], [1, v2], ...], ...
// PositionalRowsJson  : row-major, schema declared once, rows are tuples
//                       ["Schema", "col1", ...], [v1, v2, ...], ...
// ArrayOfObjectsJson  : row-major, conventional REST API response,
//                       one JSON object per row, column names are repeated
//                       [{"col": v1, ...}, ...]
// Auto                : not a layout — let the layouter pick the best format
enum class ResultFormat {
  ColumnarJson,TypedColumnarJson, IndexedColumnarJson,
  PositionalRowsJson, ArrayOfObjectsJson, Auto
};

// Per-column metadata
struct ColumnData {
  std::vector<std::string> names;
  std::vector<bool> dateCols;
  std::vector<size_t> heights;
  std::vector<ArgsPtr> cells;
  size_t ncols = 0;
  size_t nrows = 0;
};

ExprPtr parseExpression(const nlohmann::json& value, QueryFormat format, std::string& error);

// If the expression's top-level operator is exactly
// Slice(<inner>, ["Int", offset], ["Int", count]) - the likely query 
// agents use for follow-up pagination calls - returns `offset`
// Nullopt for anything else
std::optional<size_t> detectSliceOffset(const BOSSExpression* expression);

// Same detection as detectSliceOffset, but takes the RAW pre-parse json
// and returns the json for `inner` alone
std::optional<nlohmann::json> detectSliceInnerJson(const nlohmann::json& value, QueryFormat format);

// labelOffset biases IndexedColumnarJson's row labels
// Pass detectSliceOffset's result to keep a follow-up page's labels
// absolute relative to the query the agent originally paged from
nlohmann::json expressionToJson(const BOSSExpression* expression, ResultFormat format,
                                size_t labelOffset = 0);

// Extract table metadata from BOSS expression
// Nullopt if expression isn't a Table, or is one whose columns aren't all Complex
// Callers fall back to toTypedColumnarJson, which represents everything.
std::optional<ColumnData> extractTable(const BOSSExpression* expression);

// Serializes rows [rowOffset, rowOffset + rowCount) of `data` in the given format
// TypedColumnarJson is treated as ColumnarJson if passed as it is the general
// expression encoding, not a table layout
nlohmann::json serializeTable(const ColumnData& data, ResultFormat format,
                              size_t rowOffset, size_t rowCount, size_t labelOffset = 0);

// BOSS's native expression representation - the fallback for any non-Table.
nlohmann::json toTypedColumnarJson(const BOSSExpression* expression);
