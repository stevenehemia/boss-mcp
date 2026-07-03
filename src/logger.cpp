#include <iostream>
#include <string>
#include "logger.h"
#include "transport.h"
#include "nlohmann/json.hpp"


namespace {

bool shouldLog(LogLevel current, LogLevel message) {
  if(current == LogLevel::Silent) return false;
  return static_cast<int>(message) >= static_cast<int>(current);
}

const char* levelToName(LogLevel level) {
  switch(level) {
    case LogLevel::Debug: return "debug";
    case LogLevel::Warn: return "warn";
    case LogLevel::Error: return "error";
    default: return "info";
  }
}

} // namespace


LogLevel parseLogLevel(const std::string& value) {
  const std::string lowered = toLower(value);
  if(lowered == "debug") return LogLevel::Debug;
  if(lowered == "warn" || lowered == "warning") return LogLevel::Warn;
  if(lowered == "error") return LogLevel::Error;
  if(lowered == "silent" || lowered == "none") return LogLevel::Silent;
  return LogLevel::Info;
}


void logMessage(const LogLevel& logLevel, LogLevel messageLevel, const std::string& text) {

  if(!shouldLog(logLevel, messageLevel)) return;
  std::cerr << text << std::endl;

  nlohmann::json notification;
  notification["jsonrpc"] = "2.0";
  notification["method"] = "notifications/message";
  notification["params"] = {{"level", levelToName(messageLevel)}, {"data", text}};
  sendResponse(notification);
}