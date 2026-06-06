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
  gmtime_r(&t, &tm_buf);
  char buf[11];
  strftime(buf, sizeof(buf), "%Y-%m-%d", &tm_buf);
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

void freeArgs(std::vector<BOSSExpression*>& args) {
  for(BOSSExpression* e : args) {
    freeBOSSExpression(e);
  }
}

BOSSExpression* buildComplex(const std::string& headName, std::vector<BOSSExpression*>& args) {
  BOSSSymbol* head = symbolNameToNewBOSSSymbol(headName.c_str());
  BOSSExpression* result = newComplexBOSSExpression(head, args.size(), args.data());
  freeBOSSSymbol(head);
  freeArgs(args);
  return result;
}

std::string getStringValue(const BOSSExpression* expr) {
  char* raw = getNewStringValueFromBOSSExpression(expr);
  std::string s = raw ? raw : "";
  freeBOSSString(raw);
  return s;
}

std::string getSymbolValue(const BOSSExpression* expr) {
  // freeBOSSString takes char* but the API returns const char* — cast required
  const char* raw = getNewSymbolNameFromBOSSExpression(expr);
  std::string s = raw ? raw : "";
  freeBOSSString(const_cast<char*>(raw));
  return s;
}

std::string getHeadName(const BOSSExpression* expr) {
  BOSSSymbol* head = getHeadFromBOSSExpression(expr);
  // freeBOSSString takes char* but the API returns const char* — cast required
  const char* raw = bossSymbolToNewString(head);
  std::string s = raw ? raw : "";
  freeBOSSSymbol(head);
  freeBOSSString(const_cast<char*>(raw));
  return s;
}

// --- ExpressionJSON (Wolfram array format) ---

// Returns nullptr with empty error if type is not a known atom — caller falls through to complex.
// Returns nullptr with non-empty error on a type mismatch.
BOSSExpression* parseAtom(const std::string& type, const json& val, std::string& error) {
  if(type == "Boolean") {
    if(!val.is_boolean()) {
      error = "Boolean requires a boolean value";
      return nullptr;
    }
    return boolToNewBOSSExpression(val.get<bool>());
  }
  if(type == "Integer") {
    if(val.is_number_integer()) { return longToNewBOSSExpression(val.get<int64_t>()); }
    if(val.is_string()) {
      try {
        return longToNewBOSSExpression(std::stoll(val.get<std::string>()));
      } catch(const std::exception&) {
        error = "Integer string is not a valid integer";
        return nullptr;
      }
    }
    error = "Integer requires an integer or string value";
    return nullptr;
  }
  if(type == "Real") {
    if(val.is_number()) { return doubleToNewBOSSExpression(val.get<double>()); }
    if(val.is_string()) {
      try {
        return doubleToNewBOSSExpression(std::stod(val.get<std::string>()));
      } catch(const std::exception&) {
        error = "Real string is not a valid number"; return nullptr;
      }
    }
    error = "Real requires a numeric or string value"; return nullptr;
  }
  if(type == "String") {
    if(!val.is_string()) { error = "String requires a string value"; return nullptr; }
    return stringToNewBOSSExpression(val.get<std::string>().c_str());
  }
  if(type == "Symbol") {
    if(!val.is_string()) { error = "Symbol requires a string value"; return nullptr; }
    return symbolNameToNewBOSSExpression(val.get<std::string>().c_str());
  }
  return nullptr; // not a known atom type — caller treats as complex expression head
}

BOSSExpression* parseExpressionJSON(const json& value, std::string& error) {
  if(!value.is_array() || value.empty()) {
    error = "ExpressionJSON must be a non-empty array";
    return nullptr;
  }

  // typed atom: ["Type", value]
  if(value.size() == 2 && value[0].is_string()) {
    BOSSExpression* atom = parseAtom(value[0].get<std::string>(), value[1], error);
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

  std::vector<BOSSExpression*> args;
  args.reserve(value.size() - 1);
  for(size_t i = 1; i < value.size(); ++i) {
    BOSSExpression* arg = parseExpressionJSON(value[i], error);
    if(!arg) { freeArgs(args); return nullptr; }
    args.push_back(arg);
  }
  return buildComplex(headName, args);
}

// --- Regular JSON (object format) ---

BOSSExpression* parseExpressionRegular(const json& value, std::string& error) {
  if(!value.is_object()) { error = "expression must be an object"; return nullptr; }

  const std::string type = value.value("type", "");

  if(type == "bool") {
    if(!value.contains("value") || !value["value"].is_boolean()) {
      error = "bool expression requires boolean value"; return nullptr;
    }
    return boolToNewBOSSExpression(value["value"].get<bool>());
  }
  if(type == "char") {
    if(!value.contains("value") || !value["value"].is_number_integer()) {
      error = "char expression requires integer value"; return nullptr;
    }
    const long long raw = value["value"].get<long long>();
    if(raw < std::numeric_limits<int8_t>::min() || raw > std::numeric_limits<int8_t>::max()) {
      error = "char expression value out of range"; return nullptr;
    }
    return charToNewBOSSExpression(static_cast<int8_t>(raw));
  }
  if(type == "int") {
    if(!value.contains("value") || !value["value"].is_number_integer()) {
      error = "int expression requires integer value"; return nullptr;
    }
    const long long raw = value["value"].get<long long>();
    if(raw < std::numeric_limits<int32_t>::min() || raw > std::numeric_limits<int32_t>::max()) {
      error = "int expression value out of range"; return nullptr;
    }
    return intToNewBOSSExpression(static_cast<int32_t>(raw));
  }
  if(type == "long") {
    if(!value.contains("value") || !value["value"].is_number_integer()) {
      error = "long expression requires integer value"; return nullptr;
    }
    const long long raw = value["value"].get<long long>();
    if(raw < std::numeric_limits<int64_t>::min() || raw > std::numeric_limits<int64_t>::max()) {
      error = "long expression value out of range"; return nullptr;
    }
    return longToNewBOSSExpression(static_cast<int64_t>(raw));
  }
  if(type == "float") {
    if(!value.contains("value") || !value["value"].is_number()) {
      error = "float expression requires numeric value"; return nullptr;
    }
    return floatToNewBOSSExpression(static_cast<float>(value["value"].get<double>()));
  }
  if(type == "double") {
    if(!value.contains("value") || !value["value"].is_number()) {
      error = "double expression requires numeric value"; return nullptr;
    }
    return doubleToNewBOSSExpression(value["value"].get<double>());
  }
  if(type == "string") {
    if(!value.contains("value") || !value["value"].is_string()) {
      error = "string expression requires string value"; return nullptr;
    }
    return stringToNewBOSSExpression(value["value"].get<std::string>().c_str());
  }
  if(type == "symbol") {
    if(!value.contains("value") || !value["value"].is_string()) {
      error = "symbol expression requires string value"; return nullptr;
    }
    return symbolNameToNewBOSSExpression(value["value"].get<std::string>().c_str());
  }
  if(type == "call") {
    if(!value.contains("head") || !value["head"].is_string()) {
      error = "call expression requires string head"; return nullptr;
    }
    if(!value.contains("args") || !value["args"].is_array()) {
      error = "call expression requires array args"; return nullptr;
    }
    std::vector<BOSSExpression*> args;
    args.reserve(value["args"].size());
    for(const auto& arg : value["args"]) {
      BOSSExpression* expr = parseExpressionRegular(arg, error);
      if(!expr) { freeArgs(args); return nullptr; }
      args.push_back(expr);
    }
    return buildComplex(value["head"].get<std::string>(), args);
  }

  error = "unsupported expression type";
  return nullptr;
}

} // namespace

BOSSExpression* parseExpression(const json& value, Format format, std::string& error) {
  if(format == Format::ExpressionJSON) return parseExpressionJSON(value, error);
  return parseExpressionRegular(value, error);
}

json expressionToJson(const BOSSExpression* expression, Format format) {
  const int typeID = getBOSSExpressionTypeID(expression);

  if(typeID == TypeID::Complex) {
    std::string head = getHeadName(expression);
    bool dateCol = isDateColumnName(head);
    size_t count = getArgumentCountFromBOSSExpression(expression);
    BOSSExpression** args = getArgumentsFromBOSSExpression(expression);
    json children = json::array();
    for(size_t i = 0; i < count; ++i) {
      if(dateCol && getBOSSExpressionTypeID(args[i]) == TypeID::Int) {
        std::string iso = epochDayToIso(getIntValueFromBOSSExpression(args[i]));
        children.push_back(format == Format::ExpressionJSON
          ? json::array({"String", iso})
          : json{{"type", "string"}, {"value", iso}});
      } else {
        children.push_back(expressionToJson(args[i], format));
      }
    }
    freeBOSSArguments(args);
    if(format == Format::ExpressionJSON) {
      children.insert(children.begin(), head);
      return children;
    }
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
