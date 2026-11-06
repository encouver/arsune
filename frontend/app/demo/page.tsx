import SiteHeader from "../site-header";

export default function Demo() {
  return (
    <>
      <SiteHeader current="/demo" />
      <main>
        <section className="intro">
          <div>
            <p className="eyebrow">LOCAL DEMO</p>
            <h1>
              Try both checks.
              <br />
              <span>Read the evidence.</span>
            </h1>
            <p className="lead">
              Run real checks on safe local examples. All demo checks use
              scripted mode. No model key is required.
            </p>
            <p className="notice">
              Results come from each new check. Incident records can support a
              possible cause. They do not prove a cause or a repair.
            </p>
          </div>
          <div className="scan-panel">
            <h2>1. Website faults and repair</h2>
            <p>
              Select Site with faults. Check the confirmed broken link, mobile
              overflow, and price difference. The checker must reject the
              labeled false claim.
            </p>
            <p>
              Then select Compare with corrected demo. Check which faults were
              resolved and download the repair report.
            </p>
            <p>
              <a href="/#website-demo">Open website demo</a>
            </p>
            <hr />
            <h2>2. Incident cause and control</h2>
            <p>
              Load the known failure, then select Investigate incident. Check
              the missing column and failed migration in the source records.
              Download the incident report.
            </p>
            <p>
              <a href="/incidents?example=known">Load known incident</a>
            </p>
            <p>
              Repeat with the unrelated timeout. Supported explanations must be
              empty. A nearby deployment alone is not evidence of a cause.
            </p>
            <p>
              <a href="/incidents?example=control">
                Load unrelated timeout control
              </a>
            </p>
          </div>
        </section>
      </main>
    </>
  );
}
