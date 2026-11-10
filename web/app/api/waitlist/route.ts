import { ipAddress, waitUntil } from "@vercel/functions";
import { NextResponse, type NextRequest } from "next/server";
import { createAdminClient } from "../../../lib/supabase/admin";
import { notifyTeamOfSignup } from "../../../lib/notify-signup";
import { validateSignup, type WaitlistSignup } from "../../../lib/waitlist";

export const runtime = "nodejs";

// The waitlist join endpoint (#153 review, blocker 3). The browser posts here instead of calling
// Supabase's join_waitlist RPC directly with the anon key: that let anyone holding the public key skip
// this route entirely and supply whatever IP they liked to the rate limiter. Only this server can call
// join_waitlist now (see the migration: execute is service_role only), and the IP it passes comes from
// Vercel's ipAddress() helper, not a client-suppliable header -- Vercel's edge sets x-forwarded-for
// itself on every request a Function sees, overwriting whatever the client sent.
export async function POST(request: NextRequest) {
  const body: unknown = await request.json().catch(() => null);
  if (!body || typeof body !== "object") {
    return NextResponse.json({ code: "bad_request" }, { status: 400 });
  }

  const field = (name: string): string => {
    const value = (body as Record<string, unknown>)[name];
    return typeof value === "string" ? value : "";
  };
  const signup: WaitlistSignup = {
    firstName: field("firstName"),
    lastName: field("lastName"),
    email: field("email"),
    role: field("role"),
    source: field("source"),
  };
  if (Object.keys(validateSignup(signup)).length > 0) {
    return NextResponse.json({ code: "invalid" }, { status: 400 });
  }

  // Local `next dev` isn't running on Vercel, so this is null there and every local signup shares the
  // rate limiter's 'unknown' bucket -- documented as a known, local-only limitation in the migration.
  const ip = ipAddress(request) ?? null;
  // Deployed on Vercel this should never be null; if it is, every visitor lands in the shared 'unknown' bucket
  // and five signups would lock everyone out, so make that loud instead of silent.
  if (ip === null && process.env.VERCEL) console.error("waitlist: no client IP from ipAddress() on Vercel");

  const { data: inserted, error } = await createAdminClient().rpc("join_waitlist", {
    p_first_name: signup.firstName.trim(),
    p_last_name: signup.lastName.trim(),
    p_email: signup.email.trim(),
    p_role: signup.role,
    p_source: signup.source,
    p_ip_address: ip,
  });

  if (!error) {
    // #287: only on a genuinely new row, never a duplicate absorbed by ON CONFLICT DO NOTHING. waitUntil()
    // lets this finish after the response below is sent, without making the visitor wait on an email; the
    // .catch() means a failed notification never turns a successful signup into an error response.
    if (inserted) {
      waitUntil(
        notifyTeamOfSignup({
          firstName: signup.firstName.trim(),
          lastName: signup.lastName.trim(),
          role: signup.role,
          source: signup.source,
        }).catch((err: unknown) => console.error("waitlist: signup notification failed", err)),
      );
    }
    return NextResponse.json({ ok: true });
  }
  if (error.code === "EDR01") return NextResponse.json({ code: "EDR01" }, { status: 429 });
  // Keep Postgres' code and message server-side: echoing them to the browser leaks schema and constraint names.
  console.error("waitlist: join_waitlist failed", error.code, error.message);
  return NextResponse.json({ code: "failed" }, { status: 500 });
}
