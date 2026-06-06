#pragma once
#include <string>
#include "BOSS.h"
#include "nlohmann/json.hpp"

enum class Format { ExpressionJSON, Regular };

BOSSExpression* parseExpression(const nlohmann::json& value, Format format, std::string& error);
nlohmann::json expressionToJson(const BOSSExpression* expression, Format format);
