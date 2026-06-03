#pragma once
#include "expression.h"
#include "logger.h"
#include "nlohmann/json.hpp"

struct HandlerResult {
  bool shouldExit     = false;
  bool shouldShutDown = false;
};

HandlerResult handleRequest(const nlohmann::json& request, LoggerState& logger, Format format);
