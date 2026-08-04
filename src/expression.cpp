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

// TypedColumnarJSON: A Complex becomes ["Head", arg, ...]; an atom becomes
// ["Type", value]. This is BOSS's general ExpressionJSON encoding -- it can
// represent *any* result (table, bare scalar, error), which is why every
// table-only format below falls back to it, and why it alone keeps a head tag.
// Date columns (int32 days-since-epoch) are rendered back to ISO strings.
// dateCol means this expression sits inside a date-named column.
// A Complex node computes the flag from its own head for its children.
json toTypedColumnarJson(const BOSSExpression* expression, bool dateCol = false) {

  const int typeID = getBOSSExpressionTypeID(expression);

  if(typeID == TypeID::Complex) {
    std::string head = getHeadName(expression);
    bool childrenAreDates = isDateColumnName(head);
    size_t argCount = getArgumentCountFromBOSSExpression(expression);
    ArgsPtr args(getArgumentsFromBOSSExpression(expression));

    // Head leads the array; reserve so a wide column (hundreds of thousands of
    // rows) avoids reallocation and an O(n) front-insert.
    json children = json::array();
    children.get_ref<json::array_t&>().reserve(argCount + 1);
    children.push_back(head);
    for(size_t i = 0; i < argCount; ++i) {
      children.push_back(toTypedColumnarJson(args.get()[i], childrenAreDates));
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
    case TypeID::Complex: return toTypedColumnarJson(expression);
    default: return json(nullptr);
  }
}

// Per-column metadata + owned argument pointers, pivoted once out of a Table's
// Complex columns and shared by all four table-only serializers below -- they
// need the same name/date-flag/height/cells extraction, and differ only in the
// JSON shape they build from it. expressionToJson does the extraction once and
// hands the result down, so the serializers below are pure ColumnData -> json.
struct ColumnData {
  std::vector<std::string> names;
  std::vector<bool> dateCols;
  std::vector<size_t> heights;
  std::vector<ArgsPtr> cells;  // owns each column's argument array
  size_t ncols = 0;
  size_t nrows = 0;  // row count from the first column
};

// The cell at (row r, column c) as a plain JSON value.
//
// A well-formed Table has every column the same height, but nothing enforces
// that here, so `nrows` is taken from the first column and every serializer
// reads through this one accessor: short columns read as null past their end,
// long ones are truncated by the caller's `r < d.nrows` bound. Going through a
// single accessor is what keeps all four formats presenting a malformed Table
// *identically* -- previously the two column-major ones iterated each column's
// own height instead and emitted ragged output where the row-major two padded.
json cellAt(const ColumnData& d, size_t r, size_t c) {
  if(r >= d.heights[c]) return json(nullptr);
  return cellToPlainJson(d.cells[c].get()[r], d.dateCols[c]);
}

bool isTableExpression(const BOSSExpression* expression) {
  return getBOSSExpressionTypeID(expression) == TypeID::Complex &&
         getHeadName(expression) == "Table";
}

// Nullopt (caller falls back to toTypedColumnarJson) if any column isn't
// Complex -- not a well-formed Table.
std::optional<ColumnData> extractColumns(const BOSSExpression* table) {
  ColumnData out;
  out.ncols = getArgumentCountFromBOSSExpression(table);
  ArgsPtr colArgs(getArgumentsFromBOSSExpression(table));
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

// ArrayOfObjectsJson: what a conventional REST API would return (array of row
// objects). Pivot the columnar BOSS Table expression
// Table[ col1[v...], col2[v...], ... ] into row-major records
// [{"col1": v, "col2": v, ...}, ...] -- so every column name repeats once per
// record, which is what makes this the most expensive format on tokens.
json tableToArrayOfObjectsJson(const ColumnData& d) {
  json rows = json::array();
  rows.get_ref<json::array_t&>().reserve(d.nrows);

  for(size_t r = 0; r < d.nrows; ++r) {
    json obj = json::object();
    for(size_t c = 0; c < d.ncols; ++c) {
      obj[d.names[c]] = cellAt(d, r, c);
    }
    rows.push_back(std::move(obj));
  }

  return rows;
}

// ColumnarJson: pure columnar (BOSS's own DSM layout), per-value type tags
// dropped -- ["col1", v1, v2, ...], ["col2", v1, v2, ...], ...
json tableToColumnarJson(const ColumnData& d) {
  json cols = json::array();
  cols.get_ref<json::array_t&>().reserve(d.ncols);
  for(size_t c = 0; c < d.ncols; ++c) {
    json col = json::array();
    col.get_ref<json::array_t&>().reserve(d.nrows + 1);
    col.push_back(d.names[c]);
    for(size_t r = 0; r < d.nrows; ++r) {
      col.push_back(cellAt(d, r, c));
    }
    cols.push_back(std::move(col));
  }
  return cols;
}

// IndexedColumnarJson: columnar, tags dropped, each cell additionally paired
// with its row position -- ["col1", [0, v0], [1, v1], ...], ... -- so a
// value can be bound back to its row by matching indices directly instead of
// counting positions across separate parallel arrays.
json tableToIndexedColumnarJson(const ColumnData& d) {
  json cols = json::array();
  cols.get_ref<json::array_t&>().reserve(d.ncols);
  for(size_t c = 0; c < d.ncols; ++c) {
    json col = json::array();
    col.get_ref<json::array_t&>().reserve(d.nrows + 1);
    col.push_back(d.names[c]);
    for(size_t r = 0; r < d.nrows; ++r) {
      col.push_back(json::array({static_cast<int64_t>(r), cellAt(d, r, c)}));
    }
    cols.push_back(std::move(col));
  }
  return cols;
}

// PositionalRowsJson: schema declared once, then rows as positional
// value-tuples -- ["Schema", "col1", "col2", ...], [v1, v2, ...], ... --
// the format actually isomorphic to classical NSM (schema lives once, not
// repeated per row the way ArrayOfObjectsJson's object keys do).
json tableToPositionalRowsJson(const ColumnData& d) {
  json schema = json::array();
  schema.get_ref<json::array_t&>().reserve(d.ncols + 1);
  schema.push_back("Schema");
  for(size_t c = 0; c < d.ncols; ++c) schema.push_back(d.names[c]);

  json out = json::array();
  out.get_ref<json::array_t&>().reserve(d.nrows + 1);
  out.push_back(std::move(schema));
  for(size_t r = 0; r < d.nrows; ++r) {
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


json expressionToJson(const BOSSExpression* expression, ResultFormat format) {
  // Every format but TypedColumnarJson is only defined for a well-formed Table.
  // Anything else -- a scalar, an ErrorWhenEvaluatingExpression, a Table whose
  // columns aren't Complex -- falls through to the typed form, which can
  // represent any expression. That single fallthrough is the only place this
  // decision is made; the serializers below never see a malformed input.
  if(format != ResultFormat::TypedColumnarJson && isTableExpression(expression)) {
    if(std::optional<ColumnData> d = extractColumns(expression)) {
      switch(format) {
        case ResultFormat::ColumnarJson: return tableToColumnarJson(*d);
        case ResultFormat::IndexedColumnarJson: return tableToIndexedColumnarJson(*d);
        case ResultFormat::PositionalRowsJson: return tableToPositionalRowsJson(*d);
        case ResultFormat::ArrayOfObjectsJson: return tableToArrayOfObjectsJson(*d);
        case ResultFormat::TypedColumnarJson: break;  // excluded by the guard above
      }
    }
  }
  return toTypedColumnarJson(expression);
}
