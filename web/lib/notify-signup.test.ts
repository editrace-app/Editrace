import { afterEach, beforeEach, expect, test, vi } from "vitest";

const { sendMail, createTransport } = vi.hoisted(() => {
  const sendMail = vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({}));
  return { sendMail, createTransport: vi.fn(() => ({ sendMail })) };
});
vi.mock("nodemailer", () => ({ default: { createTransport } }));

import { notifyTeamOfSignup } from "./notify-signup";

const signup = { firstName: "Ada", lastName: "Lovelace", role: "editor", source: "tiktok" };

beforeEach(() => {
  sendMail.mockClear();
  createTransport.mockClear();
  vi.stubEnv("GMAIL_USER", "editrace.co@gmail.com");
  vi.stubEnv("GMAIL_APP_PASSWORD", "app-password");
  vi.stubEnv("WAITLIST_NOTIFY_EMAIL", "editrace.co@gmail.com");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

test("sends an email with the signup's name, role and source, never the email address", async () => {
  await notifyTeamOfSignup(signup);
  expect(createTransport).toHaveBeenCalledWith({
    service: "gmail",
    auth: { user: "editrace.co@gmail.com", pass: "app-password" },
  });
  expect(sendMail).toHaveBeenCalledWith(
    expect.objectContaining({
      from: "editrace.co@gmail.com",
      to: "editrace.co@gmail.com",
      subject: expect.stringContaining("waitlist"),
      text: expect.stringContaining("Ada Lovelace"),
    }),
  );
  const { text } = sendMail.mock.calls[0][0] as { text: string };
  expect(text).toContain("editor");
  expect(text).toContain("tiktok");
  expect(text).not.toMatch(/@/); // no email address anywhere in the body
});

test.each([
  ["GMAIL_USER"],
  ["GMAIL_APP_PASSWORD"],
  ["WAITLIST_NOTIFY_EMAIL"],
])("skips sending, without throwing, when %s is not set", async (missing) => {
  vi.stubEnv(missing, "");
  await expect(notifyTeamOfSignup(signup)).resolves.toBeUndefined();
  expect(sendMail).not.toHaveBeenCalled();
});

test("a rejected send propagates to the caller to handle (route.ts logs it via waitUntil)", async () => {
  sendMail.mockRejectedValueOnce(new Error("SMTP connection refused"));
  await expect(notifyTeamOfSignup(signup)).rejects.toThrow("SMTP connection refused");
});
