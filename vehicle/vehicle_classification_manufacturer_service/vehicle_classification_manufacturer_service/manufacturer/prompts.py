"""Vehicle attribute prompt aligned with the standalone validation script."""

PROMPT = (
    "Identify the vehicle's consumer brand, body type, and dominant exterior paint color. "
    'vehicle_type must be exactly one of: sedan, hatchback, suv, wagon, coupe, convertible, van, '
    'pickup, bus, truck, motorcycle, bicycle, unknown. Use van for minivans, bus for minibuses '
    'and coaches, motorcycle for scooters, and wagon for station wagons. Use unknown if the body '
    'type cannot be determined. Do not invent other vehicle_type labels. For vehicle body color, '
    'allow only: white, black, red, blue, green, yellow, orange, unknown. Apply these '
    'application-specific mappings consistently: gray and silver to white; brown to black; '
    'off-white and ivory to white; burgundy and maroon to red; navy to blue; olive to green. '
    'Identify the dominant painted body color. Ignore windows, tyres, wheels, lights, plates, '
    'background and reflections. Return unknown when the image does not provide enough reliable '
    'color evidence, including infrared-only footage. Do not map unknown or invalid responses to '
    'white or black. Return exactly one JSON object, without Markdown code fences or explanations, '
    'with these keys: manufacturer, vehicle_type, color. '
    'Example: {"manufacturer":"toyota","vehicle_type":"sedan","color":"white"}.'
)
