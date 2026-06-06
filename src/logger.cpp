#include <iostream>
#include <string>
#include "logger.h"
#include "transport.h"
#include "nlohmann/json.hpp"

namespace {

bool shouldLog(LogLevel current, LogLevel message) {
  if(current == LogLevel::kSilent) { return false; }
  return static_cast<int>(message) >= static_cast<int>(current);
}

const char* levelToName(LogLevel level) {
  switch(level) {
    case LogLevel::kDebug: return "debug";
    case LogLevel::kWarn: return "warn";
    case LogLevel::kError: return "error";
    default: return "info";
  }
}
} // namespace

LogLevel parseLogLevel(const std::string& value) {
  const std::string lowered = toLower(value);
  if(lowered == "debug") { return LogLevel::kDebug; }
  if(lowered == "warn" || lowered == "warning") { return LogLevel::kWarn; }
  if(lowered == "error") { return LogLevel::kError; }
  if(lowered == "silent" || lowered == "none") { return LogLevel::kSilent; }
  return LogLevel::kInfo;
}

void logMessage(const LogLevel& logLevel, LogLevel messageLevel, const std::string& text) {
  if(!shouldLog(logLevel, messageLevel)) { return; }
  std::cerr << text << std::endl;

  nlohmann::json notification;
  notification["jsonrpc"] = "2.0";
  notification["method"] = "notifications/message";
  notification["params"] = {{"level", levelToName(messageLevel)}, {"data", text}};
  sendResponse(notification);
}