export default function AnalysisSettings({
  schema,
  values,
  onChange,
  busy,
  saved,
}) {
  if (!schema) {
    return (
      <div className="lab-settings-loading" role="status">
        Loading analysis defaults from the server…
      </div>
    );
  }
  const defaults = Object.fromEntries(
    schema.fields.map((field) => [field.key, field.default]),
  );
  const changed = schema.fields.filter(
    (field) => Number(values[field.key]) !== field.default,
  ).length;
  const groups = [...new Set(schema.fields.map((field) => field.group))];
  return (
    <details className="lab-settings">
      <summary>
        <span>
          <span className="lab-settings-icon">⚙</span> Analysis settings{" "}
          <small>{changed ? `${changed} modified` : "Server defaults"}</small>
        </span>
        <span className="lab-settings-summary">
          Tune the next run <span>⌄</span>
        </span>
      </summary>
      <div className="lab-settings-body">
        <div className="lab-settings-toolbar">
          <p>
            Changes apply only to the next video. Defaults come from the running
            pipeline.
          </p>
          <div>
            {saved && (
              <button
                type="button"
                className="lab-button lab-button-light"
                disabled={busy}
                onClick={() =>
                  onChange(
                    Object.fromEntries(
                      schema.fields.map((field) => [
                        field.key,
                        saved[field.key] ?? field.default,
                      ]),
                    ),
                  )
                }
              >
                Use displayed run’s settings
              </button>
            )}
            <button
              type="button"
              className="lab-button lab-button-light"
              disabled={busy || !changed}
              onClick={() => onChange(defaults)}
            >
              Reset defaults
            </button>
          </div>
        </div>
        <fieldset disabled={busy} className="lab-settings-fields">
          {groups.map((group) => (
            <section
              className="lab-settings-group"
              key={group}
              aria-label={`${group} settings`}
            >
              <h3>{group}</h3>
              <div className="lab-settings-grid">
                {schema.fields
                  .filter((field) => field.group === group)
                  .map((field) => (
                    <label className="lab-setting" key={field.key}>
                      <span>
                        {field.label}
                        <small>Default {field.default}</small>
                      </span>
                      <input
                        type="number"
                        required
                        min={field.min}
                        max={field.max}
                        step={field.type === "int" ? 1 : "any"}
                        value={values[field.key] ?? ""}
                        onInvalid={(event) => {
                          event.target.closest("details").open = true;
                        }}
                        onChange={(event) =>
                          onChange({
                            ...values,
                            [field.key]: event.target.value,
                          })
                        }
                      />
                      <small>{field.help}</small>
                    </label>
                  ))}
              </div>
            </section>
          ))}
        </fieldset>
        <div className="lab-settings-fixed">
          <span className="lab-label">MODEL CONSTRAINTS · FIXED</span>
          {schema.fixed.map((item) => (
            <div key={item.label}>
              <strong>
                {item.label}
                <span>{item.value}</span>
              </strong>
              <small>{item.reason}</small>
            </div>
          ))}
        </div>
      </div>
    </details>
  );
}
