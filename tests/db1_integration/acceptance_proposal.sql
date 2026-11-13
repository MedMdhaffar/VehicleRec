-- PROPOSED service-owned supporting schema v1, for isolated P4 tests only.
-- Not part of supervisor DB1; needs separate platform review before deployment.
CREATE SCHEMA db1_delivery;
CREATE TABLE db1_delivery.compatibility (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    metadata bytea NOT NULL
);
CREATE TABLE db1_delivery.acceptance (
    org_id uuid NOT NULL REFERENCES public.dim_org,
    logical_identity bytea NOT NULL,
    payload bytea NOT NULL,
    digest text NOT NULL CHECK (length(digest)=64),
    PRIMARY KEY (org_id,logical_identity)
);
CREATE TABLE db1_delivery.row_receipt (
    org_id uuid NOT NULL,
    logical_identity bytea NOT NULL,
    row_kind text NOT NULL CHECK (row_kind IN ('fact_vehicle','fact_vehicle_colour','fact_plate_read')),
    uid uuid NOT NULL,
    delivery bytea NOT NULL,
    projection bytea NOT NULL,
    delivery_sha256 text NOT NULL CHECK (length(delivery_sha256)=64),
    projection_sha256 text NOT NULL CHECK (length(projection_sha256)=64),
    PRIMARY KEY (row_kind,uid),
    FOREIGN KEY (org_id,logical_identity) REFERENCES db1_delivery.acceptance
);
ALTER TABLE db1_delivery.acceptance ENABLE ROW LEVEL SECURITY;
ALTER TABLE db1_delivery.acceptance FORCE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON db1_delivery.acceptance
    USING (org_id=NULLIF(current_setting('app.org_id',true),'')::uuid)
    WITH CHECK (org_id=NULLIF(current_setting('app.org_id',true),'')::uuid);
ALTER TABLE db1_delivery.row_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE db1_delivery.row_receipt FORCE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON db1_delivery.row_receipt
    USING (org_id=NULLIF(current_setting('app.org_id',true),'')::uuid)
    WITH CHECK (org_id=NULLIF(current_setting('app.org_id',true),'')::uuid);
