#pragma once
#include <string>
#include "nlohmann/json.hpp"
#include "transport.h"

/**
 * MCP defined log levels by severity:
 * - debug: detailed internal state
 * - info: normal operations and high-level events
 * - warn: recoverable issues
 * - error: serious issues or something has failed
 */
enum class LogLevel { kDebug, kInfo, kWarn, kError, kSilent };

struct LoggerState {
  LogLevel level = LogLevel::kInfo;
  bool clientInitialized = false;
};

LogLevel parseLogLevel(const std::string& value);
bool shouldLog(LogLevel currentLevel, LogLevel messageLevel);
void logMessage(LoggerState& state, LogLevel level, const std::string& text);