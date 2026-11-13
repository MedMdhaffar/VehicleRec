"""Optional shared-foundation imports for repository and Django application roots.

Standalone CLI use does not import this module. Integrated hosts must make the
shared `common` package available (vehicle/ on sys.path), or import from the
repository root using vehicle.common. Never load fixture data or infer context.
"""
try:
    from common.frame_receipt import ClockEpochInvalid, FrameReceiptClock
    from common.vehicle_observation import RunEnd, VehicleClosure, VehicleLifecycle
    from common.vehicle_frame import VehicleBoxObservation, VehicleFrame
except ModuleNotFoundError as exc:
    if exc.name != "common":
        raise
    from vehicle.common.frame_receipt import ClockEpochInvalid, FrameReceiptClock
    from vehicle.common.vehicle_observation import RunEnd, VehicleClosure, VehicleLifecycle
    from vehicle.common.vehicle_frame import VehicleBoxObservation, VehicleFrame
