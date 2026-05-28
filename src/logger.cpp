#include <cctype>
#include <iostream>
#include <map>
#include <string>
#include "logger.h"
#include "transport.h"
#include "nlohmann/json.hpp"

const std::map<std::string, LogLevel> kLevelByName = {
    {"debug", LogLevel::kDebug},
    {"info", LogLevel::kInfo},
    {"warn", LogLevel::kWarn},
    {"warning", LogLevel::kWarn},
    {"error", LogLevel::kError},
    {"silent", LogLevel::kSilent},
    {"none", LogLevel::kSilent},
};

const std::string kDefaultLevelName = "info";

LogLevel parseLogLevel(const std::string& value) {
  std::string lowered = toLower(value);
  auto it = kLevelByName.find(lowered);
  if(it != kLevelByName.end()) { return it->second; }
  return LogLevel::kInfo;
}

bool shouldLog(LogLevel current, LogLevel message) {
  if(current == LogLevel::kSilent) { return false; }
  return static_cast<int>(message) >= static_cast<int>(current);
}

void logMessage(LoggerState& state, LogLevel level, const std::string& text) {
  if(!shouldLog(state.level, level) || !state.clientInitialized) { return; }
  std::cerr << text << std::endl;

  std::string levelName = kDefaultLevelName;
  for(const auto& entry : kLevelByName) {
    if(entry.second == level) {
      levelName = entry.first;
      break;
    }
  }

  json notification;
  notification["jsonrpc"] = "2.0";
  notification["method"] = "notifications/message";
  notification["params"] = {{"level", levelName}, {"data", text}};
  sendResponse(notification);
}