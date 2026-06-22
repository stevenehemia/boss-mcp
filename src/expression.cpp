#include <ctime>
#include <limits>
#include <string>
#include <vector>
#include "expression.h"
#include "transport.h"
#include "BOSS.h"
#include "nlohmann/json.hpp"

extern "C" char const* bossSymbolToNewString(struct BOSSSymbol const* arg);

using json = nlohmann::json;

// Type IDs as returned by getBOSSExpressionTypeID()
namespace TypeID {
  enum { Bool, Char, Int, Long, Float, Double, String, Symbol, Complex };
}

namespace {

// Arrow date32 stores days since 1970-01-01 as int32_t.
// Convert to "YYYY-MM-DD".
std::string epochDayToIso(int32_t days) {
  time_t t = static_cast<time_t>(days) * 86400;
  struct tm tm_buf = {};
  // gmtime_r fails on out-of-range values; strftime returns 0 if buf is too
  // small (years past 9999). Bail to "" rather than read an unterminated buf.
  if(gmtime_r(&t, &tm_buf) == nullptr) { return ""; }
  char buf[32];
  if(strftime(buf, sizeof(buf), "%Y-%m-%d", &tm_buf) == 0) { return ""; }
  return buf;
}

// Columns whose name is "date", ends with "_date", or starts with "date_"
// (case-insensitive) are treated as date32 columns and their int32 values
// are converted back to ISO strings on output.
bool isDateColumnName(const std::string& name) {
  std::string lower = toLower(name);
  return lower == "date"
      || (lower.size() > 5 && lower.substr(lower.size() - 5) == "_date")
      || (lower.size() > 5 && lower.substr(0, 5) == "date_");
}

ExprPtr buildComplex(const std::string& headName, std::vector<ExprPtr>& args) {
  SymbolPtr head(symbolNameToNewBOSSSymbol(headName.c_str()));
  std::vector<BOSSExpression*> raw;
  raw.reserve(args.size());
  for(const ExprPtr& arg : args) { raw.push_back(arg.get()); }
  // newComplexBOSSExpression copies its inputs; head and args free on return.
  return ExprPtr(newComplexBOSSExpression(head.get(), raw.size(), raw.data()));
}

std::string getStringValue(const BOSSExpression* expr) {
  return toString(getNewStringValueFromBOSSExpression(expr));
}

std::string getSymbolValue(const BOSSExpression* expr) {
  // The API returns const char* but freeBOSSString takes char* — cast required.
  return toString(const_cast<char*>(getNewSymbolNameFromBOSSExpression(expr)));
}

std::string getHeadName(const BOSSExpression* expr) {
  SymbolPtr head(getHeadFromBOSSExpression(expr));
  // The API returns const char* but freeBOSSString takes char* — cast required.
  return toString(const_cast<char*>(bossSymbolToNewString(head.get())));
}

// True if raw fits in the integer type T.
template<typename T>
bool inRange(long long raw) {
  return raw >= std::numeric_limits<T>::min() && raw <= std::numeric_limits<T>::max();
}

// --- ExpressionJSON (Wolfram array format) ---

// Returns nullptr with empty error if type is not a known atom — caller falls through to complex.
// Returns nullptr with non-empty error on a type mismatch.
ExprPtr parseAtom(const std::string& type, const json& val, std::string& error) {
  if(type == "Boolean") {
    if(!val.is_boolean()) { error = "Boolean requires a boolean value"; return nullptr; }
    return ExprPtr(boolToNewBOSSExpression(val.get<bool>()));
  }
  if(type == "Int") {
    if(!val.is_number_integer()) { error = "Int requires an integer value"; return nullptr; }
    const long long raw = val.get<long long>();
    if(!inRange<int32_t>(raw)) { error = "Int value out of int32 range"; return nullptr; }
    return ExprPtr(intToNewBOSSExpression(static_cast<int32_t>(raw)));
  }
  if(type == "Integer") {
    if(val.is_number_integer()) { return ExprPtr(longToNewBOSSExpression(val.get<int64_t>())); }
    if(val.is_string()) {
      try {
        return ExprPtr(longToNewBOSSExpression(std::stoll(val.get<std::string>())));
      } catch(const std::exception&) {
        error = "Integer string is not a valid integer"; return nullptr;
      }
    }
    error = "Integer requires an integer or string value";
    return nullptr;
  }
  if(type == "Real") {
    if(val.is_number()) { return ExprPtr(doubleToNewBOSSExpression(val.get<double>())); }
    if(val.is_string()) {
      try {
        return ExprPtr(doubleToNewBOSSExpression(std::stod(val.get<std::string>())));
      } catch(const std::exception&) {
        error = "Real string is not a valid number"; return nullptr;
      }
    }
    error = "Real requires a numeric or string value"; return nullptr;
  }
  if(type == "String") {
    if(!val.is_string()) { error = "String requires a string value"; return nullptr; }
    return ExprPtr(stringToNewBOSSExpression(val.get<std::string>().c_str()));
  }
  if(type == "Symbol") {
    if(!val.is_string()) { error = "Symbol requires a string value"; return nullptr; }
    return ExprPtr(symbolNameToNewBOSSExpression(val.get<std::string>().c_str()));
  }
  return nullptr; // not a known atom type — caller treats as complex expression head
}

ExprPtr parseExpressionJSON(const json& value, std::string& error) {
  if(!value.is_array() || value.empty()) {
    error = "ExpressionJSON must be a non-empty array";
    return nullptr;
  }

  // typed atom: ["Type", value]
  if(value.size() == 2 && value[0].is_string()) {
    ExprPtr atom = parseAtom(value[0].get<std::string>(), value[1], error);
    if(atom || !error.empty()) { return atom; }
  }

  // complex expression: ["Head", arg1, arg2, ...]
  std::string headName;
  if(value[0].is_string()) {
    headName = value[0].get<std::string>();
  } else if(value[0].is_array() && value[0].size() == 2 &&
            value[0][0].is_string() && value[0][0].get<std::string>() == "Symbol" &&
            value[0][1].is_string()) {
    headName = value[0][1].get<std::string>();
  } else {
    error = "ExpressionJSON head must be a string or [\"Symbol\", name]";
    return nullptr;
  }

  std::vector<ExprPtr> args;
  args.reserve(value.size() - 1);
  for(size_t i = 1; i < value.size(); ++i) {
    ExprPtr arg = parseExpressionJSON(value[i], error);
    if(!arg) { return nullptr; }
    args.push_back(std::move(arg));
  }
  return buildComplex(headName, args);
}

// --- Regular JSON (object format) ---

// Returns the "value" field if present and ok(value) holds; otherwise sets
// error to msg and returns nullptr.
template<typename Predicate>
const json* requireValue(const json& obj, Predicate ok, std::string& error, const char* msg) {
  auto it = obj.find("value");
  if(it == obj.end() || !ok(*it)) { error = msg; return nullptr; }
  return &*it;
}

ExprPtr parseExpressionRegular(const json& value, std::string& error) {
  if(!value.is_object()) { error = "expression must be an object"; return nullptr; }

  const std::string type = value.value("type", "");
  const auto isInt = [](const json& j) { return j.is_number_integer(); };
  const auto isNum = [](const json& j) { return j.is_number(); };
  const auto isStr = [](const json& j) { return j.is_string(); };

  if(type == "bool") {
    const json* v = requireValue(value, [](const json& j) { return j.is_boolean(); },
                                 error, "bool expression requires boolean value");
    return v ? ExprPtr(boolToNewBOSSExpression(v->get<bool>())) : nullptr;
  }
  if(type == "char") {
    const json* v = requireValue(value, isInt, error, "char expression requires integer value");
    if(!v) { return nullptr; }
    const long long raw = v->get<long long>();
    if(!inRange<int8_t>(raw)) { error = "char expression value out of range"; return nullptr; }
    return ExprPtr(charToNewBOSSExpression(static_cast<int8_t>(raw)));
  }
  if(type == "int") {
    const json* v = requireValue(value, isInt, error, "int expression requires integer value");
    if(!v) { return nullptr; }
    const long long raw = v->get<long long>();
    if(!inRange<int32_t>(raw)) { error = "int expression value out of range"; return nullptr; }
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
      error = "call expression requires string head"; return nullptr;
    }
    if(!value.contains("args") || !value["args"].is_array()) {
      error = "call expression requires array args"; return nullptr;
    }
    std::vector<ExprPtr> args;
    args.reserve(value["args"].size());
    for(const auto& arg : value["args"]) {
      ExprPtr expr = parseExpressionRegular(arg, error);
      if(!expr) { return nullptr; }
      args.push_back(std::move(expr));
    }
    return buildComplex(value["head"].get<std::string>(), args);
  }

  error = "unsupported expression type";
  return nullptr;
}

} // namespace

ExprPtr parseExpression(const json& value, Format format, std::string& error) {
  if(format == Format::ExpressionJSON) return parseExpressionJSON(value, error);
  return parseExpressionRegular(value, error);
}

json expressionToJson(const BOSSExpression* expression, Format format) {
  const int typeID = getBOSSExpressionTypeID(expression);

  if(typeID == TypeID::Complex) {
    std::string head = getHeadName(expression);
    bool dateCol = isDateColumnName(head);
    size_t count = getArgumentCountFromBOSSExpression(expression);
    ArgsPtr args(getArgumentsFromBOSSExpression(expression));
    const bool isExprJson = format == Format::ExpressionJSON;

    // For ExpressionJSON the head leads the array; push it first and reserve so
    // a wide column (hundreds of thousands of rows) avoids reallocation and an
    // O(n) front-insert.
    json children = json::array();
    children.get_ref<json::array_t&>().reserve(count + (isExprJson ? 1 : 0));
    if(isExprJson) { children.push_back(head); }

    for(size_t i = 0; i < count; ++i) {
      BOSSExpression* arg = args.get()[i];
      if(dateCol && getBOSSExpressionTypeID(arg) == TypeID::Int) {
        std::string iso = epochDayToIso(getIntValueFromBOSSExpression(arg));
        children.push_back(isExprJson
          ? json::array({"String", iso})
          : json{{"type", "string"}, {"value", iso}});
      } else {
        children.push_back(expressionToJson(arg, format));
      }
    }
    if(isExprJson) { return children; }
    return {{"type", "call"}, {"head", head}, {"args", children}};
  }

  if(format == Format::ExpressionJSON) {
    switch(typeID) {
      case TypeID::Bool: return json::array({"Boolean", getBoolValueFromBOSSExpression(expression)});
      case TypeID::Char: return json::array({"Integer", static_cast<int64_t>(getCharValueFromBOSSExpression(expression))});
      case TypeID::Int: return json::array({"Integer", static_cast<int64_t>(getIntValueFromBOSSExpression(expression))});
      case TypeID::Long: return json::array({"Integer", getLongValueFromBOSSExpression(expression)});
      case TypeID::Float:
      case TypeID::Double: return json::array({"Real",    getDoubleValueFromBOSSExpression(expression)});
      case TypeID::String: return json::array({"String",  getStringValue(expression)});
      case TypeID::Symbol: return json::array({"Symbol",  getSymbolValue(expression)});
      default: return json::array({"Unknown", nullptr});
    }
  }

  switch(typeID) {
    case TypeID::Bool: return {{"type", "bool"}, {"value", getBoolValueFromBOSSExpression(expression)}};
    case TypeID::Char: return {{"type", "char"}, {"value", getCharValueFromBOSSExpression(expression)}};
    case TypeID::Int: return {{"type", "int"}, {"value", getIntValueFromBOSSExpression(expression)}};
    case TypeID::Long: return {{"type", "long"}, {"value", getLongValueFromBOSSExpression(expression)}};
    case TypeID::Float: return {{"type", "float"}, {"value", getFloatValueFromBOSSExpression(expression)}};
    case TypeID::Double: return {{"type", "double"}, {"value", getDoubleValueFromBOSSExpression(expression)}};
    case TypeID::String: return {{"type", "string"}, {"value", getStringValue(expression)}};
    case TypeID::Symbol: return {{"type", "symbol"}, {"value", getSymbolValue(expression)}};
    default: return {{"type", "unknown"}, {"value", nullptr}};
  }
}
