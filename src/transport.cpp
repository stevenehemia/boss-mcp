#include <optional>
#include <string>
#include <iostream>
#include <cctype>
#include <fstream>
#include "transport.h"
#include "nlohmann/json.hpp"

using json = nlohmann::json;

static bool isFramed = true;

std::string toLower(std::string value) {
  for(char& c : value) {
    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  }
  return value;
}

std::string trim(std::string value) {
  size_t start = 0;
  while(start < value.size() && std::isspace(static_cast<unsigned char>(value[start]))) {
    ++start;
  }
  size_t end = value.size();
  while(end > start && std::isspace(static_cast<unsigned char>(value[end - 1]))) {
    --end;
  }
  return value.substr(start, end - start);
}

std::optional<std::string> readMessage(std::istream& input) {
  std::string line;
  size_t contentLength = 0;
  bool sawLength = false;

  while(true) {
    if(!std::getline(input, line)) { return std::nullopt; }

    if(!line.empty() && line.back() == '\r') { line.pop_back(); }

    if(!sawLength && !line.empty() && line.front() == '{') {
      isFramed = false;
      return line;
    }

    if(line.empty()) {
      if(sawLength) { break; }
      continue;
    }

    auto colon = line.find(':');
    if(colon == std::string::npos) { continue; }

    std::string header = toLower(trim(line.substr(0, colon)));
    std::string value = trim(line.substr(colon + 1));
    if(header == "content-length") {
      contentLength = static_cast<size_t>(std::stoul(value));
      sawLength = true;
    }
  }

  if(!sawLength) { return std::nullopt; }

  isFramed = true;

  std::string payload(contentLength, '\0');
  input.read(payload.data(), static_cast<std::streamsize>(contentLength));

  if(!input) { return std::nullopt; }

  return payload;
}

json makeError(int code, const std::string& message, const json& id) {
  json response;
  response["jsonrpc"] = "2.0";
  response["id"] = id;
  response["error"] = {{"code", code}, {"message", message}};
  return response;
}

json makeResult(const json& id, const json& result) {
  json response;
  response["jsonrpc"] = "2.0";
  response["id"] = id;
  response["result"] = result;
  return response;
}

std::string buildFrame(const json& message) {
  const std::string payload = message.dump();
  if(!isFramed) { return payload + "\n"; }
  return "Content-Length: " + std::to_string(payload.size()) + "\r\n\r\n" + payload;
}

void sendResponse(const json& message) {
  std::cout << buildFrame(message);
  std::cout.flush();
}