#include <limits>
#include <vector>
#include "expression.h"
#include "BOSS.h"
#include "nlohmann/json.hpp"

using json = nlohmann::json;

BOSSExpression* parseExpression(const json& value, std::string& error) {
  if(!value.is_object()) {
    error = "expression must be an object";
    return nullptr;
  }

  const std::string type = value.value("type", "");
  if(type == "bool") {
    if(!value.contains("value") || !value["value"].is_boolean()) {
      error = "bool expression requires boolean value";
      return nullptr;
    }
    return boolToNewBOSSExpression(value["value"].get<bool>());
  }
  if(type == "char") {
    if(!value.contains("value") || !value["value"].is_number_integer()) {
      error = "char expression requires integer value";
      return nullptr;
    }
    const long long raw = value["value"].get<long long>();
    if(raw < std::numeric_limits<int8_t>::min() || raw > std::numeric_limits<int8_t>::max()) {
      error = "char expression value out of range";
      return nullptr;
    }
    return charToNewBOSSExpression(static_cast<int8_t>(raw));
  }
  if(type == "int") {
    if(!value.contains("value") || !value["value"].is_number_integer()) {
      error = "int expression requires integer value";
      return nullptr;
    }
    const long long raw = value["value"].get<long long>();
    if(raw < std::numeric_limits<int32_t>::min() || raw > std::numeric_limits<int32_t>::max()) {
      error = "int expression value out of range";
      return nullptr;
    }
    return intToNewBOSSExpression(static_cast<int32_t>(raw));
  }
  if(type == "long") {
    if(!value.contains("value") || !value["value"].is_number_integer()) {
      error = "long expression requires integer value";
      return nullptr;
    }
    const long long raw = value["value"].get<long long>();
    if(raw < std::numeric_limits<int64_t>::min() || raw > std::numeric_limits<int64_t>::max()) {
      error = "long expression value out of range";
      return nullptr;
    }
    return longToNewBOSSExpression(static_cast<int64_t>(raw));
  }
  if(type == "float") {
    if(!value.contains("value") || !value["value"].is_number()) {
      error = "float expression requires numeric value";
      return nullptr;
    }
    return floatToNewBOSSExpression(static_cast<float>(value["value"].get<double>()));
  }
  if(type == "double") {
    if(!value.contains("value") || !value["value"].is_number()) {
      error = "double expression requires numeric value";
      return nullptr;
    }
    return doubleToNewBOSSExpression(value["value"].get<double>());
  }
  if(type == "string") {
    if(!value.contains("value") || !value["value"].is_string()) {
      error = "string expression requires string value";
      return nullptr;
    }
    return stringToNewBOSSExpression(value["value"].get<std::string>().c_str());
  }
  if(type == "symbol") {
    if(!value.contains("value") || !value["value"].is_string()) {
      error = "symbol expression requires string value";
      return nullptr;
    }
    return symbolNameToNewBOSSExpression(value["value"].get<std::string>().c_str());
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
    std::vector<BOSSExpression*> args;
    args.reserve(value["args"].size());
    for(const auto& arg : value["args"]) {
      std::string argError;
      BOSSExpression* expr = parseExpression(arg, argError);
      if(expr == nullptr) {
        error = argError;
        for(BOSSExpression* created : args) {
          freeBOSSExpression(created);
        }
        return nullptr;
      }
      args.push_back(expr);
    }
    BOSSSymbol* head = symbolNameToNewBOSSSymbol(value["head"].get<std::string>().c_str());
    BOSSExpression* result = newComplexBOSSExpression(head, args.size(), args.data());
    freeBOSSSymbol(head);
    for(BOSSExpression* created : args) {
      freeBOSSExpression(created);
    }
    return result;
  }

  error = "unsupported expression type";
  return nullptr;
}

json expressionToJson(const BOSSExpression* expression) {
  json output;
  switch(getBOSSExpressionTypeID(expression)) {
    case 0: {
      output["type"] = "bool";
      output["value"] = getBoolValueFromBOSSExpression(expression);
      break;
    }
    case 1: {
      output["type"] = "char";
      output["value"] = getCharValueFromBOSSExpression(expression);
      break;
    }
    case 2: {
      output["type"] = "int";
      output["value"] = getIntValueFromBOSSExpression(expression);
      break;
    }
    case 3: {
      output["type"] = "long";
      output["value"] = getLongValueFromBOSSExpression(expression);
      break;
    }
    case 4: {
      output["type"] = "float";
      output["value"] = getFloatValueFromBOSSExpression(expression);
      break;
    }
    case 5: {
      output["type"] = "double";
      output["value"] = getDoubleValueFromBOSSExpression(expression);
      break;
    }
    case 6: {
      output["type"] = "string";
      char* value = getNewStringValueFromBOSSExpression(expression);
      output["value"] = value ? value : "";
      freeBOSSString(value);
      break;
    }
    case 7: {
      output["type"] = "symbol";
      const char* value = getNewSymbolNameFromBOSSExpression(expression);
      output["value"] = value ? value : "";
      freeBOSSString(const_cast<char*>(value));
      break;
    }
    case 8: {
      output["type"] = "call";
      BOSSSymbol* head = getHeadFromBOSSExpression(expression);
      const char* headName = bossSymbolToNewString(head);
      output["head"] = headName ? headName : "";
      freeBOSSSymbol(head);
      freeBOSSString(const_cast<char*>(headName));
      size_t count = getArgumentCountFromBOSSExpression(expression);
      BOSSExpression** args = getArgumentsFromBOSSExpression(expression);
      output["args"] = json::array();
      for(size_t i = 0; i < count; ++i) {
        output["args"].push_back(expressionToJson(args[i]));
      }
      freeBOSSArguments(args);
      break;
    }
    default:
      output["type"] = "unknown";
      output["value"] = nullptr;
      break;
  }
  return output;
}