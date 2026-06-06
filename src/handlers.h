#pragma once
#include "expression.h"
#include "logger.h"
#include "nlohmann/json.hpp"

bool handleRequest(const nlohmann::json& request, LogLevel& logLevel, Format format);
