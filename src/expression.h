#pragma once
#include <string>
#include "BOSS.h"
#include "boss_raii.h"
#include "nlohmann/json.hpp"

// How the agent's query (tool input) is encoded.
//   ArrayJson  : positional arrays    ["Op", arg, ...]   / atoms ["String","s"]
//   ObjectJson : tagged objects        {"type":"call","head":"Op","args":[...]}
enum class QueryFormat { ArrayJson, ObjectJson };

// How the result Table (tool output) is laid out.
//   ColumnarJson        : column-major, type tags dropped   ["col", v1, v2, ...], ...
//   TypedColumnarJson   : column-major ExpressionJSON  ["Table", ["col", ["Type", v1], ...], ...]
//   IndexedColumnarJson : column-major, tags dropped, each cell paired with its
//                         row index   ["col", [0, v1], [1, v2], ...], ...
//   PositionalRowsJson  : row-major, schema declared once, rows as positional
//                         value-tuples   ["Schema", "col1", ...], [v1, v2, ...], ...
//   ArrayOfObjectsJson  : row-major, one JSON object per row, so the column
//                         names repeat on every record   [{"col": v1, ...}, ...]
// The two row-major formats differ in how a value is addressed: by position
// (PositionalRowsJson) or by key (ArrayOfObjectsJson). PositionalRowsJson is the
// one isomorphic to classical NSM -- schema stored once, not repeated per tuple.
//
// Everything except TypedColumnarJson is table-only by construction and carries
// no "Table" head. TypedColumnarJson is BOSS's general expression encoding --
// it represents every result shape including errors and bare scalars, so its
// head tag is the only way to tell those apart, and expressionToJson falls back
// to it for any non-Table result. That fallback is exactly what lets the other
// four skip the head: a client tells them apart by whether the parsed top-level
// value starts with a string (typed: Table/error/scalar) or not.
enum class ResultFormat {
  ColumnarJson, TypedColumnarJson, IndexedColumnarJson, PositionalRowsJson, ArrayOfObjectsJson
};

ExprPtr parseExpression(const nlohmann::json& value, QueryFormat format, std::string& error);
nlohmann::json expressionToJson(const BOSSExpression* expression, ResultFormat format);
