#pragma once
#include "logger.h"
#include "nlohmann/json.hpp"

struct HandlerResult {
  bool shouldExit     = false;
  bool shouldShutDown = false;
};

HandlerResult handleRequest(const nlohmann::json& request, LoggerState& logger);
nlohmann::json buildToolsList();
nlohmann::json handleToolsCall(const nlohmann::json& params, LoggerState& logger);
