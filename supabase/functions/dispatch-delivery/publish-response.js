export const classifyEmqxPublishStatus = (status) => {
  if (status === 200) return "DELIVERED";
  if (status === 202) return "NO_MATCHING_SUBSCRIBERS";
  // These responses reject the request before EMQX can publish it. Keep
  // timeout/conflict/early-data responses conservative because an upstream
  // proxy may have lost the final broker response after accepting the body.
  if ([400, 401, 403, 404, 405, 413, 415, 422, 429].includes(status)) {
    return "DEFINITIVE_REJECTION";
  }
  return "UNKNOWN";
};
