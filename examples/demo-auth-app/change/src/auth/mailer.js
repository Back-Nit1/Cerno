export const outbox = [];

export function mailerConfig() {
  return {
    host: process.env.SMTP_HOST,
    user: process.env.SMTP_USER,
    password: process.env.SMTP_PASSWORD,
  };
}

export function sendMail(to, subject, body) {
  const { host } = mailerConfig();
  outbox.push({ to, subject, body, host });
}
