#pragma once
#include "BOSS.h"
#include "nlohmann/json.hpp"

extern "C" char const* bossSymbolToNewString(struct BOSSSymbol const* arg);
BOSSExpression* parseExpression(const nlohmann::json& value, std::string& error);
nlohmann::json expressionToJson(const BOSSExpression* expression);