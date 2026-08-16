#include <algorithm>
#include <ctime>
#include <limits>
#include <optional>
#include <string>
#include <vector>
#include "expression.h"
#include "strutil.h"
#include "BOSS.h"
#include "nlohmann/json.hpp"

extern "C" char const* bossSymbolToNewString(struct BOSSSymbol const* arg);

using json = nlohmann::json;

namespace {

// Maps the plain integers returned by getBOSSExpressionTypeID() to enum
// values for clarity.
enum TypeID { Bool, Char, Int, Long, Float, Double, String, Symbol, Complex };

// Arrow date32 stores days since 1970-01-01 as int32_t.
// Convert to "YYYY-MM-DD".
std::string epochDayToIso(int32_t days) {
  // Widen before multiplying: days * 86400 in 32-bit overflows past 2038.
  time_t t = static_cast<time_t>(days) * 86400;
  struct tm tm_buf = {};
  // gmtime_r fails on out-of-range values; strftime returns 0 if buf is too
  // small (years past 9999). Bail to "" rather than read an unterminated buf.
  if(gmtime_r(&t, &tm_buf) == nullptr) return "";
  char buf[32];
  if(strftime(buf, sizeof(buf), "%Y-%m-%d", &tm_buf) == 0) return "";
  return buf;
}

// Columns whose name is "date", ends with "_date", or starts with "date_"
// (case-insensitive) are treated as date32 columns and their int32 values
// are converted back to ISO strings on output.
bool isDateColumnName(const std::string& name) {
  std::string lower = toLower(name);
  return lower == "date" ||
         (lower.size() > 5 && lower.substr(lower.size() - 5) == "_date") ||
         (lower.size() > 5 && lower.substr(0, 5) == "date_");
}


ExprPtr buildComplex(const std::string& headName, std::vector<ExprPtr>& args) {
  SymbolPtr head(symbolNameToNewBOSSSymbol(headName.c_str()));
  std::vector<BOSSExpression*> raw;
  raw.reserve(args.size());
  for(const ExprPtr& arg : args) {
    raw.push_back(arg.get());
  }
  // newComplexBOSSExpression copies its inputs; head and args free on return.
  return ExprPtr(newComplexBOSSExpression(head.get(), raw.size(), raw.data()));
}


std::string getStringValue(const BOSSExpression* expr) {
  return toString(getNewStringValueFromBOSSExpression(expr));
}


std::string getSymbolValue(const BOSSExpression* expr) {
  // Cast required because the API returns const char* but freeBOSSString takes char*.
  return toString(const_cast<char*>(getNewSymbolNameFromBOSSExpression(expr)));
}


std::string getHeadName(const BOSSExpression* expr) {
  SymbolPtr head(getHeadFromBOSSExpression(expr));
  // Cast required because the API returns const char* but freeBOSSString takes char*.
  return toString(const_cast<char*>(bossSymbolToNewString(head.get())));
}

// True if raw fits in the integer type T.
template <typename T>
bool inRange(long long raw) {
  return raw >= std::numeric_limits<T>::min() &&
         raw <= std::numeric_limits<T>::max();
}

// Returns nullptr with empty error if type is not a known atom.
// Returns nullptr with non-empty error on a type mismatch.
ExprPtr parseAtom(const std::string& type, const json& val, std::string& error) {

  if(type == "Boolean") {
    if(!val.is_boolean()) {
      error = "Boolean requires a boolean value";
      return nullptr;
    }
    return ExprPtr(boolToNewBOSSExpression(val.get<bool>()));
  }
  // "Int" forces a 32-bit value for width-sensitive operators (e.g. Slice
  // offset/count); "Integer" is the general-purpose int64 atom.
  if(type == "Int") {
    if(!val.is_number_integer()) {
      error = "Int requires an integer value";
      return nullptr;
    }
    const long long raw = val.get<long long>();
    if(!inRange<int32_t>(raw)) {
      error = "Int value out of int32 range";
      return nullptr;
    }
    return ExprPtr(intToNewBOSSExpression(static_cast<int32_t>(raw)));
  }
  if(type == "Integer") {
    if(val.is_number_integer()) {
      return ExprPtr(longToNewBOSSExpression(val.get<int64_t>()));
    }
    if(val.is_string()) {
      try {
        return ExprPtr(longToNewBOSSExpression(std::stoll(val.get<std::string>())));
      } catch(const std::exception&) {
        error = "Integer string is not a valid integer";
        return nullptr;
      }
    }
    error = "Integer requires an integer or string value";
    return nullptr;
  }
  if(type == "Real") {
    if(val.is_number()) {
      return ExprPtr(doubleToNewBOSSExpression(val.get<double>()));
    }
    if(val.is_string()) {
      try {
        return ExprPtr(doubleToNewBOSSExpression(std::stod(val.get<std::string>())));
      } catch(const std::exception&) {
        error = "Real string is not a valid number";
        return nullptr;
      }
    }
    error = "Real requires a numeric or string value";
    return nullptr;
  }
  if(type == "String") {
    if(!val.is_string()) {
      error = "String requires a string value";
      return nullptr;
    }
    return ExprPtr(stringToNewBOSSExpression(val.get<std::string>().c_str()));
  }
  if(type == "Symbol") {
    if(!val.is_string()) {
      error = "Symbol requires a string value";
      return nullptr;
    }
    return ExprPtr(symbolNameToNewBOSSExpression(val.get<std::string>().c_str()));
  }

  return nullptr;  // not a known atom type, caller treats as complex expression head
}


ExprPtr parseArrayJsonExpression(const json& value, std::string& error) {
  
  if(!value.is_array() || value.empty()) {
    error = "ExpressionJSON must be a non-empty array";
    return nullptr;
  }

  if(value.size() == 2 && value[0].is_string()) {
    ExprPtr atom = parseAtom(value[0].get<std::string>(), value[1], error);
    if(atom || !error.empty()) return atom;
  }

  std::string headName;
  if(value[0].is_string()) {
    headName = value[0].get<std::string>();
  } else if(value[0].is_array() &&
            value[0].size() == 2 &&
            value[0][0].is_string() &&
            value[0][0].get<std::string>() == "Symbol" &&
            value[0][1].is_string()) {
    headName = value[0][1].get<std::string>();
  } else {
    error = "Array JSON head must be a string or [\"Symbol\", name]";
    return nullptr;
  }

  std::vector<ExprPtr> args;
  args.reserve(value.size() - 1);
  for(size_t i = 1; i < value.size(); ++i) {
    ExprPtr arg = parseArrayJsonExpression(value[i], error);
    if(!arg) return nullptr;
    args.push_back(std::move(arg));
  }

  return buildComplex(headName, args);
}

// Returns the "value" field if present and ok(value) holds; otherwise sets
// error to msg and returns nullptr.
template <typename Predicate>
const json* requireValue(const json& obj, Predicate ok, std::string& error, const char* msg) {
  auto it = obj.find("value");
  if(it == obj.end() || !ok(*it)) {
    error = msg;
    return nullptr;
  }
  return &*it;
}


ExprPtr parseObjectJsonExpression(const json& value, std::string& error) {

  if(!value.is_object()) {
    error = "expression must be an object";
    return nullptr;
  }

  const std::string type = value.value("type", "");

  const auto isBool = [](const json& j) { return j.is_boolean(); };
  const auto isInt = [](const json& j) { return j.is_number_integer(); };
  const auto isNum = [](const json& j) { return j.is_number(); };
  const auto isStr = [](const json& j) { return j.is_string(); };

  if(type == "bool") {
    const json* v = requireValue(value, isBool, error, "bool expression requires boolean value");
    return v ? ExprPtr(boolToNewBOSSExpression(v->get<bool>())) : nullptr;
  }
  if(type == "char") {
    const json* v = requireValue(value, isInt, error, "char expression requires integer value");
    if(!v) return nullptr;
    const long long raw = v->get<long long>();
    if(!inRange<int8_t>(raw)) {
      error = "char expression value out of range";
      return nullptr;
    }
    return ExprPtr(charToNewBOSSExpression(static_cast<int8_t>(raw)));
  }
  if(type == "int") {
    const json* v = requireValue(value, isInt, error, "int expression requires integer value");
    if(!v) return nullptr;
    const long long raw = v->get<long long>();
    if(!inRange<int32_t>(raw)) {
      error = "int expression value out of range";
      return nullptr;
    }
    return ExprPtr(intToNewBOSSExpression(static_cast<int32_t>(raw)));
  }
  if(type == "long") {
    const json* v = requireValue(value, isInt, error, "long expression requires integer value");
    return v ? ExprPtr(longToNewBOSSExpression(v->get<int64_t>())) : nullptr;
  }
  if(type == "float") {
    const json* v = requireValue(value, isNum, error, "float expression requires numeric value");
    return v ? ExprPtr(floatToNewBOSSExpression(static_cast<float>(v->get<double>()))) : nullptr;
  }
  if(type == "double") {
    const json* v = requireValue(value, isNum, error, "double expression requires numeric value");
    return v ? ExprPtr(doubleToNewBOSSExpression(v->get<double>())) : nullptr;
  }
  if(type == "string") {
    const json* v = requireValue(value, isStr, error, "string expression requires string value");
    return v ? ExprPtr(stringToNewBOSSExpression(v->get<std::string>().c_str())) : nullptr;
  }
  if(type == "symbol") {
    const json* v = requireValue(value, isStr, error, "symbol expression requires string value");
    return v ? ExprPtr(symbolNameToNewBOSSExpression(v->get<std::string>().c_str())) : nullptr;
  }
  if(type == "call") {
    if(!value.contains("head") || !value["head"].is_string()) {
      error = "call expression requires string head";
      return nullptr;
    }
    if(!value.contains("args") || !value["args"].is_array()) {
      error = "call expression requires array args";
      return nullptr;
    }
    std::vector<ExprPtr> args;
    args.reserve(value["args"].size());
    for(const auto& arg : value["args"]) {
      ExprPtr expr = parseObjectJsonExpression(arg, error);
      if(!expr) return nullptr;
      args.push_back(std::move(expr));
    }
    return buildComplex(value["head"].get<std::string>(), args);
  }

  error = "unsupported expression type";
  return nullptr;
}

// TypedColumnarJSON: BOSS' general expression encoding
// A Complex becomes ["Head", arg, ...]; an atom becomes ["Type", value]
// Date columns (int32 days-since-epoch) are rendered back to ISO strings
// dateCol means this expression sits inside a date-named column
json toTypedColumnarJsonRec(const BOSSExpression* expression, bool dateCol) {

  const int typeID = getBOSSExpressionTypeID(expression);

  if(typeID == TypeID::Complex) {
    std::string head = getHeadName(expression);
    bool childrenAreDates = isDateColumnName(head);
    size_t argCount = getArgumentCountFromBOSSExpression(expression);
    ArgsPtr args(getArgumentsFromBOSSExpression(expression));

    json children = json::array();
    children.get_ref<json::array_t&>().reserve(argCount + 1);
    children.push_back(head);
    for(size_t i = 0; i < argCount; ++i) {
      children.push_back(toTypedColumnarJsonRec(args.get()[i], childrenAreDates));
    }
    return children;
  }

  switch(typeID) {
    case TypeID::Bool:
      return json::array({"Boolean", getBoolValueFromBOSSExpression(expression)});
    case TypeID::Char:
      return json::array({"Integer", static_cast<int64_t>(getCharValueFromBOSSExpression(expression))});
    case TypeID::Int:
      if(dateCol) {
        return json::array({"String", epochDayToIso(getIntValueFromBOSSExpression(expression))});
      }
      return json::array({"Integer", static_cast<int64_t>(getIntValueFromBOSSExpression(expression))});
    case TypeID::Long:
      return json::array({"Integer", getLongValueFromBOSSExpression(expression)});
    case TypeID::Float:
    case TypeID::Double:
      return json::array({"Real", getDoubleValueFromBOSSExpression(expression)});
    case TypeID::String:
      return json::array({"String", getStringValue(expression)});
    case TypeID::Symbol:
      return json::array({"Symbol", getSymbolValue(expression)});
    default: return json::array({"Unknown", nullptr});
  }
}


// A single cell rendered as a plain (untyped) JSON value.
// Dates become ISO strings.
json cellToPlainJson(const BOSSExpression* expression, bool dateCol) {

  const int typeID = getBOSSExpressionTypeID(expression);
  
  switch(typeID) {
    case TypeID::Bool: return getBoolValueFromBOSSExpression(expression);
    case TypeID::Char: return static_cast<int64_t>(getCharValueFromBOSSExpression(expression));
    case TypeID::Int:
      if(dateCol) return epochDayToIso(getIntValueFromBOSSExpression(expression));
      return static_cast<int64_t>(getIntValueFromBOSSExpression(expression));
    case TypeID::Long: return getLongValueFromBOSSExpression(expression);
    case TypeID::Float:
    case TypeID::Double: return getDoubleValueFromBOSSExpression(expression);
    case TypeID::String: return getStringValue(expression);
    case TypeID::Symbol: {
      std::string s = getSymbolValue(expression);
      if(s == "NULL") return json(nullptr);  // BOSS stores nulls as the Symbol NULL
      return s;
    }
    // A cell shouldn't be complex in a result table; preserve it columnar.
    case TypeID::Complex: return toTypedColumnarJsonRec(expression, false);
    default: return json(nullptr);
  }
}

// The cell at (row r, column c) as a plain JSON value.
json cellAt(const ColumnData& d, size_t r, size_t c) {
  if(r >= d.heights[c]) return json(nullptr);
  return cellToPlainJson(d.cells[c].get()[r], d.dateCols[c]);
}

// Pivot the columnar BOSS Table expression
// Table[ col1[v...], col2[v...], ... ] into row-major records
// [{"col1": v, "col2": v, ...}, ...]
json tableToArrayOfObjectsJson(const ColumnData& d, size_t begin, size_t end) {
  json rows = json::array();
  rows.get_ref<json::array_t&>().reserve(end - begin);

  for(size_t r = begin; r < end; ++r) {
    json obj = json::object();
    for(size_t c = 0; c < d.ncols; ++c) {
      obj[d.names[c]] = cellAt(d, r, c);
    }
    rows.push_back(std::move(obj));
  }

  return rows;
}

// indexed=false  ColumnarJson (plain)
//                ["col1", v1, v2, ...], ["col2", v1, v2, ...], ...
// indexed=true   IndexedColumnarJson -- the same, but each cell paired with
//                ["col1", [0, v0], [1, v1], ...], ... --
json tableToColumnarJson(const ColumnData& d, size_t begin, size_t end, bool indexed,
                          size_t labelOffset = 0) {
  json cols = json::array();
  cols.get_ref<json::array_t&>().reserve(d.ncols);
  for(size_t c = 0; c < d.ncols; ++c) {
    json col = json::array();
    col.get_ref<json::array_t&>().reserve(end - begin + 1);
    col.push_back(d.names[c]);
    for(size_t r = begin; r < end; ++r) {
      json cell = cellAt(d, r, c);
      if(indexed) {
        const int64_t label = static_cast<int64_t>(r - begin + labelOffset);
        col.push_back(json::array({label, std::move(cell)}));
      } else {
        col.push_back(std::move(cell));
      }
    }
    cols.push_back(std::move(col));
  }
  return cols;
}

// PositionalRowsJson: schema declared once, then rows as tuples
// ["Schema", "col1", "col2", ...], [v1, v2, ...], ... --
json tableToPositionalRowsJson(const ColumnData& d, size_t begin, size_t end) {
  json schema = json::array();
  schema.get_ref<json::array_t&>().reserve(d.ncols + 1);
  schema.push_back("Schema");
  for(size_t c = 0; c < d.ncols; ++c) schema.push_back(d.names[c]);

  json out = json::array();
  out.get_ref<json::array_t&>().reserve(end - begin + 1);
  out.push_back(std::move(schema));
  for(size_t r = begin; r < end; ++r) {
    json row = json::array();
    row.get_ref<json::array_t&>().reserve(d.ncols);
    for(size_t c = 0; c < d.ncols; ++c) {
      row.push_back(cellAt(d, r, c));
    }
    out.push_back(std::move(row));
  }
  return out;
}

}  // namespace


ExprPtr parseExpression(const json& value, QueryFormat format, std::string& error) {
  if(format == QueryFormat::ArrayJson) return parseArrayJsonExpression(value, error);
  return parseObjectJsonExpression(value, error);
}


json toTypedColumnarJson(const BOSSExpression* expression) {
  return toTypedColumnarJsonRec(expression, false);
}


std::optional<ColumnData> extractTable(const BOSSExpression* expression) {
  // Nullopt -- and the caller falls back to toTypedColumnarJson -- if this
  // isn't a Table at all, or is one whose columns aren't all Complex.
  if(getBOSSExpressionTypeID(expression) != TypeID::Complex ||
     getHeadName(expression) != "Table") {
    return std::nullopt;
  }

  ColumnData out;
  out.ncols = getArgumentCountFromBOSSExpression(expression);
  ArgsPtr colArgs(getArgumentsFromBOSSExpression(expression));
  out.names.resize(out.ncols);
  out.dateCols.resize(out.ncols);
  out.heights.resize(out.ncols);
  out.cells.resize(out.ncols);

  for(size_t c = 0; c < out.ncols; ++c) {
    BOSSExpression* col = colArgs.get()[c];
    if(getBOSSExpressionTypeID(col) != TypeID::Complex) return std::nullopt;
    out.names[c] = getHeadName(col);
    out.dateCols[c] = isDateColumnName(out.names[c]);
    out.heights[c] = getArgumentCountFromBOSSExpression(col);
    out.cells[c] = ArgsPtr(getArgumentsFromBOSSExpression(col));
  }
  if(out.ncols > 0) out.nrows = out.heights[0];
  return out;
}


json serializeTable(const ColumnData& data, ResultFormat format, size_t rowOffset,
                    size_t rowCount, size_t labelOffset) {
  const size_t begin = std::min(rowOffset, data.nrows);
  // Clamp the COUNT against what remains rather than clamping begin+rowCount:
  // the latter wraps for a large rowCount (e.g. the natural "everything from
  // here" idiom, rowCount = SIZE_MAX), yielding end < begin and an end-begin
  // underflow in the serializers' reserve() calls.
  const size_t end = begin + std::min(rowCount, data.nrows - begin);
  switch(format) {
    case ResultFormat::IndexedColumnarJson: return tableToColumnarJson(data, begin, end, true, labelOffset);
    case ResultFormat::PositionalRowsJson: return tableToPositionalRowsJson(data, begin, end);
    case ResultFormat::ArrayOfObjectsJson: return tableToArrayOfObjectsJson(data, begin, end);
    // Neither of these is a table layout: TypedColumnarJson is the general
    // expression encoding, and Auto is a request-time mode the caller should
    // have resolved to a concrete format already. Both fall back to plain
    // columnar rather than being rejected, so a caller can't accidentally get
    // no output at all.
    case ResultFormat::ColumnarJson:
    case ResultFormat::TypedColumnarJson:
    case ResultFormat::Auto: break;
  }
  return tableToColumnarJson(data, begin, end, false);
}


// Slice(<inner>, ["Int", offset], ["Int", count]) parses into a Complex node
// whose second argument is a leaf TypeID::Int node (not a nested wrapper)
std::optional<size_t> detectSliceOffset(const BOSSExpression* expression) {
  if(getBOSSExpressionTypeID(expression) != TypeID::Complex) return std::nullopt;
  if(getHeadName(expression) != "Slice") return std::nullopt;
  if(getArgumentCountFromBOSSExpression(expression) != 3) return std::nullopt;
  ArgsPtr args(getArgumentsFromBOSSExpression(expression));
  const BOSSExpression* offsetArg = args.get()[1];
  if(getBOSSExpressionTypeID(offsetArg) != TypeID::Int) return std::nullopt;
  const int32_t offset = getIntValueFromBOSSExpression(offsetArg);
  if(offset < 0) return std::nullopt;
  return static_cast<size_t>(offset);
}


// Same detection as detectSliceOffset, but returns the RAW JSON for `inner`
// (the pre-Slice portion) instead of the offset
std::optional<json> detectSliceInnerJson(const json& value, QueryFormat format) {
  if(format == QueryFormat::ArrayJson) {
    if(!value.is_array() || value.size() != 4) return std::nullopt;
    if(!value[0].is_string() || value[0].get<std::string>() != "Slice") return std::nullopt;
    if(!value[2].is_array() || value[2].size() != 2 || !value[2][0].is_string() ||
       value[2][0].get<std::string>() != "Int") {
      return std::nullopt;
    }
    return value[1];
  }
  if(!value.is_object() || value.value("type", "") != "call") return std::nullopt;
  if(value.value("head", "") != "Slice") return std::nullopt;
  if(!value.contains("args") || !value["args"].is_array() || value["args"].size() != 3) {
    return std::nullopt;
  }
  const json& args = value["args"];
  if(!args[1].is_object() || args[1].value("type", "") != "int") return std::nullopt;
  return args[0];
}


json expressionToJson(const BOSSExpression* expression, ResultFormat format, size_t labelOffset) {
  // Every format but TypedColumnarJson is only defined for a well-formed Table.
  // Anything else falls through to the typed form
  if(format != ResultFormat::TypedColumnarJson) {
    if(std::optional<ColumnData> d = extractTable(expression)) {
      return serializeTable(*d, format, 0, d->nrows, labelOffset);
    }
  }
  return toTypedColumnarJson(expression);
}
