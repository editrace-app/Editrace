import nodemailer from "nodemailer";
import { createLogger } from "./log";

const log = createLogger("waitlist");

export type SignupNotification = {
  firstName: string;
  lastName: string;
  role: string;
  source: string;
};

// Fire-and-forget from the caller's point of view: a new signup must never fail because this did (#287).
// web/app/api/waitlist/route.ts wraps this in @vercel/functions' waitUntil() so it finishes even after the
// response is sent, and only logs a rejection -- it never surfaces one to the browser.
//
// Deliberately omits the signup's email address (#287's own scope): this travels over Gmail, a less
// controlled channel than the database itself, so it carries only what the team needs for a quick read
// of waitlist activity, not another copy of the PII the migration already limits exposure to.
export async function notifyTeamOfSignup(signup: SignupNotification): Promise<void> {
  const user = process.env.GMAIL_USER;
  const pass = process.env.GMAIL_APP_PASSWORD;
  const to = process.env.WAITLIST_NOTIFY_EMAIL;
  if (!user || !pass || !to) {
    log.warn(
      "skipping the signup notification: GMAIL_USER, GMAIL_APP_PASSWORD or WAITLIST_NOTIFY_EMAIL is not set",
    );
    return;
  }
  const transporter = nodemailer.createTransport({ service: "gmail", auth: { user, pass } });
  await transporter.sendMail({
    from: user,
    to,
    subject: "New Editrace waitlist signup",
    text: [
      `${signup.firstName} ${signup.lastName} just joined the waitlist.`,
      "",
      `Role: ${signup.role}`,
      `Heard about us via: ${signup.source}`,
    ].join("\n"),
  });
}
