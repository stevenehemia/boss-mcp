#pragma once
#include <string>

/**
 * MCP defined log levels by severity:
 * - debug: detailed internal state
 * - info: normal operations and high-level events
 * - warn: recoverable issues
 * - error: serious issues or something has failed
 */
enum class LogLevel { kDebug, kInfo, kWarn, kError, kSilent };

LogLevel parseLogLevel(const std::string& value);
void logMessage(const LogLevel& logLevel, LogLevel messageLevel, const std::string& text);