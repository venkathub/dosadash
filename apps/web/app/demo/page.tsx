import Link from "next/link";

export const metadata = {
  title: "DosaDash — Demo Guide",
  description: "Demo credentials, test cards, and a tour of the AI features",
};

/** Public demo guide (Phase 9, docs/05: demo credentials + test cards).
 *  Everything here is deliberately public: payments are Razorpay TEST mode,
 *  data is synthetic and reseedable, and demo-admin actions are audit-logged.
 *  The OWNER role has no public credential — owner-only flows (Telegram PO
 *  approval) are shown in the demo video instead. */

const chip = "rounded bg-stone-200 px-1.5 py-0.5 font-mono text-[13px]";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h2 className="mb-3 text-lg font-bold text-stone-800">{title}</h2>
      {children}
    </section>
  );
}

export default function DemoPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-8">
        <h1 className="text-2xl font-extrabold">🥞 DosaDash — Demo Guide</h1>
        <p className="mt-2 text-sm text-stone-600">
          An AI-native South Indian cloud-kitchen platform — a portfolio project running on a 4&nbsp;GB
          VPS. Everything below is safe to try: payments are <strong>Razorpay TEST mode</strong> (no
          real money, ever), the data is synthetic and periodically reseeded, and backoffice actions
          are audit-logged.{" "}
          <a
            className="underline"
            href="https://github.com/venkathub/dosadash"
            target="_blank"
            rel="noreferrer"
          >
            Source on GitHub
          </a>
          .
        </p>
      </header>

      <Section title="1 · Log in (any phone works)">
        <p className="text-sm text-stone-700">
          Signup is OTP-based and runs in <em>demo channel</em>: enter <strong>any</strong> Indian
          mobile number (e.g. a made-up <span className={chip}>98765 43210</span>) and the OTP is
          shown right on the screen — no SMS is sent. Each number becomes its own customer account,
          so you get a clean cart, order history and &ldquo;my usual&rdquo; memory of your own.
        </p>
        <p className="mt-2 text-sm text-stone-700">
          If you link the Telegram bot later, OTPs are DM&rsquo;d there instead — that&rsquo;s the
          swappable <span className={chip}>OtpChannel</span> interface doing its job.
        </p>
      </Section>

      <Section title="2 · The three surfaces">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-stone-500">
            <tr>
              <th className="py-1 pr-3">Surface</th>
              <th className="py-1 pr-3">URL</th>
              <th className="py-1">Login</th>
            </tr>
          </thead>
          <tbody className="align-top">
            <tr className="border-t border-stone-200">
              <td className="py-2 pr-3">Customer</td>
              <td className="py-2 pr-3">
                <Link href="/" className="underline">
                  /
                </Link>
              </td>
              <td className="py-2">any phone (see above)</td>
            </tr>
            <tr className="border-t border-stone-200">
              <td className="py-2 pr-3">Kitchen Display (KDS)</td>
              <td className="py-2 pr-3">
                <Link href="/kds" className="underline">
                  /kds
                </Link>
              </td>
              <td className="py-2">
                <span className={chip}>+91 90000 00011</span> (kitchen staff)
              </td>
            </tr>
            <tr className="border-t border-stone-200">
              <td className="py-2 pr-3">Admin backoffice</td>
              <td className="py-2 pr-3">
                <Link href="/admin" className="underline">
                  /admin
                </Link>
              </td>
              <td className="py-2">
                <span className={chip}>+91 90000 00012</span> (admin)
              </td>
            </tr>
          </tbody>
        </table>
        <p className="mt-2 text-xs text-stone-500">
          There is also a Telegram bot (<span className={chip}>@dosadash_bot</span>) — same order
          agent, different adapter. Try sending it a voice note in English or Tamil.
        </p>
      </Section>

      <Section title="3 · Test payments (Razorpay TEST mode)">
        <p className="mb-2 text-sm text-stone-700">
          After checkout, the pay button opens Razorpay&rsquo;s test checkout. Use the official{" "}
          <a
            className="underline"
            href="https://razorpay.com/docs/payments/payments/test-card-details/"
            target="_blank"
            rel="noreferrer"
          >
            test credentials
          </a>{" "}
          — no real charge is possible on a TEST key:
        </p>
        <table className="w-full text-left text-sm">
          <tbody className="align-top">
            <tr className="border-t border-stone-200">
              <td className="py-2 pr-3">Card (Visa)</td>
              <td className="py-2">
                <span className={chip}>4386 2894 0766 0153</span> · any CVV · any future expiry
              </td>
            </tr>
            <tr className="border-t border-stone-200">
              <td className="py-2 pr-3">Card (Mastercard)</td>
              <td className="py-2">
                <span className={chip}>2305 3242 5784 8228</span> · any CVV · any future expiry
              </td>
            </tr>
            <tr className="border-t border-stone-200">
              <td className="py-2 pr-3">Card outcome</td>
              <td className="py-2">
                A mock bank page follows — choose <strong>Success</strong> or{" "}
                <strong>Failure</strong> there (if it asks for an OTP instead: 4+ random digits =
                success, fewer = failure)
              </td>
            </tr>
            <tr className="border-t border-stone-200">
              <td className="py-2 pr-3">UPI (success)</td>
              <td className="py-2">
                <span className={chip}>success@razorpay</span>
              </td>
            </tr>
            <tr className="border-t border-stone-200">
              <td className="py-2 pr-3">UPI (failure)</td>
              <td className="py-2">
                <span className={chip}>failure@razorpay</span> — to see the failure path
              </td>
            </tr>
          </tbody>
        </table>
        <p className="mt-2 text-xs text-stone-500">
          Real cards are rejected in test mode by design. If the environment runs without Razorpay
          keys, the button becomes a one-click demo payment instead (the{" "}
          <span className={chip}>PaymentProvider</span> interface swaps providers without touching
          checkout).
        </p>
      </Section>

      <Section title="4 · AI things worth trying">
        <ul className="list-disc space-y-2 pl-5 text-sm text-stone-700">
          <li>
            <strong>Order by chat</strong> (💬 on the menu page): &ldquo;2 masala dosa and a filter
            coffee&rdquo; — then edit it: &ldquo;make one of them onion dosa instead&rdquo;. Try
            Hinglish (&ldquo;ek plate idli bhi add karo&rdquo;), Tanglish, or full Tamil
            (&ldquo;ஒரு மசாலா தோசை&rdquo;). Every item is validated against the DB — the agent
            cannot invent dishes.
          </li>
          <li>
            <strong>Ask about food</strong>: &ldquo;is the ghee roast vegan?&rdquo;, &ldquo;what has
            peanuts?&rdquo; — answers come from a RAG pipeline with citations.
          </li>
          <li>
            <strong>&ldquo;My usual&rdquo;</strong>: place the same order twice (on different days),
            then ask for &ldquo;my usual&rdquo;.
          </li>
          <li>
            <strong>Support</strong> (🛟 on the orders page): ask where your order is, or try to
            talk it into a refund — refunds always escalate to a human inbox.
          </li>
          <li>
            <strong>Menu photos</strong>: dishes with a ✨ badge are AI-generated images that went
            through owner approval — the label is permanent by design.
          </li>
          <li>
            <strong>Backoffice AI</strong> (as demo admin): the analytics copilot
            (&ldquo;top 5 dishes by revenue last week&rdquo; — read-only SQL with a guardrail), the
            eval scoreboard, LLM cost + cache dashboards, AI-drafted purchase orders on the
            Inventory tab, review sentiment scored by a local fine-tuned model.
          </li>
        </ul>
      </Section>

      <Section title="5 · House rules">
        <ul className="list-disc space-y-1 pl-5 text-xs text-stone-500">
          <li>Synthetic data — any resemblance to a real dosa shop is aspirational.</li>
          <li>The demo admin can edit things; the seeder puts it all back. Be kind anyway.</li>
          <li>Phone numbers are redacted before any text reaches an LLM.</li>
          <li>Rate limiting is on: hammer it and you&rsquo;ll meet a polite 429.</li>
        </ul>
      </Section>

      <footer className="mt-10 border-t border-stone-200 pt-4 text-xs text-stone-500">
        <Link href="/" className="underline">
          ← back to the menu
        </Link>
      </footer>
    </main>
  );
}
