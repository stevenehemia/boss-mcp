#pragma once
#include <string>
#include "BOSS.h"
#include "boss_raii.h"
#include "nlohmann/json.hpp"

enum class Format { ExpressionJSON, Regular };

ExprPtr parseExpression(const nlohmann::json& value, Format format, std::string& error);
nlohmann::json expressionToJson(const BOSSExpression* expression, Format format);
