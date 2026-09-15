export const MAX_FAILED_ATTEMPTS = 50;

const attempts = new Map();

export function recordFailedAttempt(email) {
  attempts.set(email, (attempts.get(email) ?? 0) + 1);
}

export function isRateLimited(email) {
  return (attempts.get(email) ?? 0) >= MAX_FAILED_ATTEMPTS;
}

export function clearAttempts(email) {
  attempts.delete(email);
}
