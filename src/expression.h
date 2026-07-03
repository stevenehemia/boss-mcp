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
//   ColumnarJson : column-major ExpressionJSON  ["Table", ["col", v1, v2, ...], ...]
//   RowRepJson   : row-major repeated records   [{"col": v1, ...}, {"col": v2, ...}, ...]
enum class ResultFormat { ColumnarJson, RowRepJson };

ExprPtr parseExpression(const nlohmann::json& value, QueryFormat format, std::string& error);
nlohmann::json expressionToJson(const BOSSExpression* expression, ResultFormat format);
