#include <optional>
#include <string>
#include "nlohmann/json.hpp"

using json = nlohmann::json;

std::string toLower(std::string value);
std::string trim(std::string value);
std::optional<std::string> readMessage(std::istream& input);
json makeError(int code, const std::string& message, const json& data);
json makeResult(const json& id, const json& result);
std::string buildFrame(const json& message);
void sendResponse(const json& message);