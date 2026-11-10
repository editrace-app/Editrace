import { beforeEach, expect, test, vi } from "vitest";
import { NextRequest } from "next/server";

type RpcError = { code: string; message: string } | null;
const { rpc, ipAddress, waitUntil, notifyTeamOfSignup } = vi.hoisted(() => ({
  rpc: vi.fn<(...args: unknown[]) => Promise<{ data: boolean | null; error: RpcError }>>(
    async () => ({ data: true, error: null }),
  ),
  ipAddress: vi.fn<(request: NextRequest) => string | undefined>(),
  // Runs the task inline, same as production for anything that resolves before the handler returns.
  waitUntil: vi.fn((promise: Promise<unknown>) => promise),
  notifyTeamOfSignup: vi.fn<(...args: unknown[]) => Promise<void>>(async () => undefined),
}));
vi.mock("../../../lib/supabase/admin", () => ({ createAdminClient: () => ({ rpc }) }));
vi.mock("@vercel/functions", () => ({ ipAddress, waitUntil }));
vi.mock("../../../lib/notify-signup", () => ({ notifyTeamOfSignup }));

import { POST } from "./route";

const valid = { firstName: "Ada", lastName: "Lovelace", email: "ada@example.com", role: "editor", source: "tiktok" };
const post = (body: unknown) => POST(new NextRequest("http://localhost:3000/api/waitlist", { method: "POST", body: JSON.stringify(body) }));

beforeEach(() => {
  rpc.mockClear();
  rpc.mockResolvedValue({ data: true, error: null });
  ipAddress.mockReset();
  ipAddress.mockReturnValue("203.0.113.5");
  waitUntil.mockClear();
  notifyTeamOfSignup.mockClear();
  notifyTeamOfSignup.mockResolvedValue(undefined);
});

test("calls join_waitlist with the trimmed fields and the IP from ipAddress()", async () => {
  const res = await post({ ...valid, firstName: " Ada ", email: " ada@example.com " });
  expect(res.status).toBe(200);
  expect(await res.json()).toEqual({ ok: true });
  expect(rpc).toHaveBeenCalledWith("join_waitlist", {
    p_first_name: "Ada",
    p_last_name: "Lovelace",
    p_email: "ada@example.com",
    p_role: "editor",
    p_source: "tiktok",
    p_ip_address: "203.0.113.5",
  });
});
test("passes null, not 'unknown', when ipAddress() finds nothing (local next dev)", async () => {
  ipAddress.mockReturnValue(undefined);
  await post(valid);
  expect(rpc).toHaveBeenCalledWith("join_waitlist", expect.objectContaining({ p_ip_address: null }));
});
test("rejects an invalid signup without calling the RPC", async () => {
  const res = await post({ ...valid, email: "not-an-email" });
  expect(res.status).toBe(400);
  expect(rpc).not.toHaveBeenCalled();
});
test("rejects a non-JSON-object body without calling the RPC", async () => {
  const res = await POST(new NextRequest("http://localhost:3000/api/waitlist", { method: "POST", body: "null" }));
  expect(res.status).toBe(400);
  expect(rpc).not.toHaveBeenCalled();
});
test("returns 429 with the EDR01 code when the rate limiter trips", async () => {
  rpc.mockResolvedValue({ data: null, error: { code: "EDR01", message: "Too many signups from this address. Try again later." } });
  const res = await post(valid);
  expect(res.status).toBe(429);
  expect(await res.json()).toMatchObject({ code: "EDR01" });
});
test("returns a generic 500, without leaking the database error, on any other RPC error", async () => {
  const log = vi.spyOn(console, "error").mockImplementation(() => {});
  rpc.mockResolvedValue({ data: null, error: { code: "23514", message: "violates check constraint waitlist_signups_role_check" } });
  const res = await post(valid);
  expect(res.status).toBe(500);
  expect(await res.json()).toEqual({ code: "failed" });
  expect(log).toHaveBeenCalledWith("waitlist: join_waitlist failed", "23514", expect.stringContaining("check constraint"));
  log.mockRestore();
});
test("logs when a Vercel deployment gets no client IP", async () => {
  const log = vi.spyOn(console, "error").mockImplementation(() => {});
  vi.stubEnv("VERCEL", "1");
  ipAddress.mockReturnValue(undefined);
  await post(valid);
  expect(log).toHaveBeenCalledWith("waitlist: no client IP from ipAddress() on Vercel");
  vi.unstubAllEnvs();
  log.mockRestore();
});

// --- #287: the team notification -----------------------------------------------------------------------------

test("notifies the team, via waitUntil, when join_waitlist reports a new row", async () => {
  rpc.mockResolvedValue({ data: true, error: null });
  const res = await post(valid);
  expect(res.status).toBe(200);
  expect(waitUntil).toHaveBeenCalledTimes(1);
  expect(notifyTeamOfSignup).toHaveBeenCalledWith({
    firstName: "Ada",
    lastName: "Lovelace",
    role: "editor",
    source: "tiktok",
  });
});

test("does not notify the team on a duplicate email (join_waitlist reports false)", async () => {
  rpc.mockResolvedValue({ data: false, error: null });
  const res = await post(valid);
  expect(res.status).toBe(200);
  expect(await res.json()).toEqual({ ok: true }); // the browser sees the same generic response either way
  expect(notifyTeamOfSignup).not.toHaveBeenCalled();
  expect(waitUntil).not.toHaveBeenCalled();
});

test("a failed notification is logged, but never turns a successful signup into an error response", async () => {
  const log = vi.spyOn(console, "error").mockImplementation(() => {});
  notifyTeamOfSignup.mockRejectedValue(new Error("SMTP connection refused"));
  const res = await post(valid);
  expect(res.status).toBe(200);
  expect(await res.json()).toEqual({ ok: true });
  await Promise.resolve(); // let the already-rejected promise's .catch() run
  expect(log).toHaveBeenCalledWith("waitlist: signup notification failed", expect.any(Error));
  log.mockRestore();
});
