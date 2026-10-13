const CONTACT = 'hello@watchfox.run'
const UPDATED = '11 October 2026'

function Page({ title, children }) {
  return (
    <div className="wrap legal">
      <span className="eyebrow">Legal</span>
      <h1>{title}</h1>
      <p className="small muted">Last updated {UPDATED}. Questions: <a href={`mailto:${CONTACT}`}>{CONTACT}</a></p>
      {children}
    </div>
  )
}

export function Privacy() {
  return (
    <Page title="Privacy policy">
      <p>WatchFox reads mail from mailboxes you connect and tells your application when it finds something you asked for. This page says what we handle, why, and for how long.</p>

      <h2>What we keep</h2>
      <ul>
        <li><b>Your account:</b> your email address and name from the sign-in provider you used (GitHub, Google or Microsoft), which provider it was, and your organization's name, plan and team. We do not store passwords.</li>
        <li><b>Mailbox connections:</b> the details needed to read a mailbox. They are stored encrypted and used only to check that mailbox for you. For Microsoft 365 and Gmail we ask for read-only access.</li>
        <li><b>A record of each check:</b> for each email a watch checks, the subject, the sender, a short snippet of up to 300 characters, the names of attachments, and the scores. We keep this for <b>90 days</b>, then delete it automatically.</li>
        <li><b>Payments:</b> if you subscribe to a paid plan, you enter your card in the secure form of our payment processor, Helcim. Card numbers go to Helcim and never reach WatchFox. We keep only a Helcim customer code, the subscription number and whether it is active.</li>
        <li><b>Your settings:</b> watches, webhook addresses, custom types and the examples you gave when tuning them.</li>
        <li><b>Emails you flag:</b> if you press “Wrong catch” or “Should have been caught”, we read that one email and keep its subject and up to 3,000 characters of text so the type can learn from it. You can delete these at any time.</li>
      </ul>

      <h2>What we do not keep</h2>
      <p>We do not store the full text of your emails, original email files or attachments. When your code asks for the original email or an attachment, it is read from the mailbox at that moment, handed to you, and not kept. Content sent inside a webhook is held in memory only while we deliver it.</p>

      <h2>Who processes email text</h2>
      <ul>
        <li>The text of each email being checked (subject, sender and up to 3,000 characters of the body) is processed by WatchFox's own checking agent.</li>
        <li>If that agent is unavailable, a backup agent run by an outside provider can be used for the same purpose. While it is in use, the text sent for checking goes to that provider, which does not promise zero retention.</li>
        <li>An AI assistant helps people set up their own types. It only receives text typed into the setup screen (a name and a description), never email content.</li>
      </ul>

      <h2>Where it runs, and who helps us run it</h2>
      <p>WatchFox is hosted on Microsoft Azure in Canada. Payments are processed by Helcim. Mailbox data and account data are stored in a PostgreSQL database there. To decide whether an email matches what you asked for, the beginning of its subject and text is sent over an encrypted private network to our own checking service, which does not keep it. If that service is unavailable, the same text may be sent to a backup checking service run by a cloud provider acting on our behalf, only for that decision. Our domain and email delivery run through Cloudflare. Signing in with GitHub, Google or Microsoft gives us only your name and email address. If email delivery is set up, invitation emails are sent through an email delivery provider; otherwise the person who invites you shares a link.</p>

      <h2>Google user data</h2>
      <p>If you sign in with Google, we receive your email address and name to create and recognize your account. We use them for nothing else, and we do not share them or use them for advertising.</p>

      <h2>Your choices</h2>
      <p>You can disconnect a mailbox, delete watches and types, remove team members, and ask us to delete your account and everything linked to it by writing to <a href={`mailto:${CONTACT}`}>{CONTACT}</a>. We do not sell data.</p>

      <h2>Security</h2>
      <p>Connections use HTTPS, credentials are encrypted at rest, webhooks are signed, and each organization can see only its own data. No system is perfectly secure, so please tell us if you see a problem.</p>
    </Page>
  )
}

export function Terms() {
  return (
    <Page title="Terms of service">
      <p>By creating an account or using WatchFox you agree to these terms.</p>

      <h2>The service</h2>
      <p>WatchFox checks emails in mailboxes you connect and reports what it finds through the website and API. It judges emails automatically, so it will sometimes be wrong. Treat a catch as a strong hint and keep a person in the loop for anything important.</p>

      <h2>Your account and organization</h2>
      <p>You are responsible for what happens in your organization, for keeping API keys and passwords safe, and for the people you invite. You may connect only mailboxes you are allowed to access, and you must have any permission required to process the email they contain.</p>

      <h2>Plans and usage</h2>
      <p>Each plan includes a monthly number of runs and limits on mailboxes, watches, custom types and team members. A run is one email checked by one watch. When the runs are used up, new mail is held back until you upgrade or the next month starts. Prices and limits may change with notice. Paid plans are billed in US dollars, in advance, every month or every year, through Helcim, until you cancel. You can cancel at any time in Settings and keep the plan until the end of the period you paid for. Fees are not refundable except where the law requires. If a payment fails, the plan stays active for a short grace period while you update your card, then your account returns to the free plan.</p>

      <h2>Acceptable use</h2>
      <p>Do not use WatchFox to break the law, to read mail you have no right to read, to harass or deceive people, to attack the service or other customers, or to try to get around limits and security.</p>

      <h2>Availability and changes</h2>
      <p>We work to keep WatchFox running but do not promise it will be uninterrupted or error free. We may change or stop features, and we may suspend accounts that break these terms or put others at risk.</p>

      <h2>Liability</h2>
      <p>The service is provided “as is”. To the extent the law allows, WatchFox is not liable for indirect or consequential losses, or for decisions made from its results, and our total liability is limited to what you paid in the three months before the claim.</p>

      <h2>Ending</h2>
      <p>You can stop using WatchFox at any time and ask us to delete your account. We may end or suspend access for breaches of these terms.</p>

      <h2>Contact</h2>
      <p><a href={`mailto:${CONTACT}`}>{CONTACT}</a></p>
    </Page>
  )
}
