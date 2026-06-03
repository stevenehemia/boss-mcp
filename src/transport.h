#pragma once
#include <optional>
#include <string>
#include "nlohmann/json.hpp"

std::string toLower(std::string value);
std::string trim(std::string value);
std::optional<std::string> readMessage(std::istream& input);
nlohmann::json makeError(int code, const std::string& message, const nlohmann::json& data);
nlohmann::json makeResult(const nlohmann::json& id, const nlohmann::json& result);
void sendResponse(const nlohmann::json& message);