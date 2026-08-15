"""The vehicle the trip is planned for.

Spotter's brief specifies four inputs and three assumptions, and this is a
fourth assumption rather than a fifth input: a standard 53-foot dry van, which
is what the overwhelming majority of property-carrying interstate freight moves
in. Asking a driver to type five dimensions before they can plan a trip would
buy accuracy nobody asked for at the cost of the brief's whole interface.

The numbers below are the US federal maxima, so a truck that differs is almost
always *smaller* -- and a route legal for the maximum is legal for anything
under it. Planning at the limit is therefore the safe direction to be wrong in.

Units are the awkward part. The regulation and the driver think in feet, inches
and pounds; OpenRouteService wants metres and metric tonnes. The conversion
lives here so exactly one place in the codebase has to be right about it.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 13 feet 6 inches. The de facto US maximum trailer height -- above this you
#: need permits, and bridge clearances stop being reliable.
DEFAULT_HEIGHT_FEET = 13.5

#: 102 inches, the federal maximum width for a commercial vehicle.
DEFAULT_WIDTH_FEET = 8.5

#: A 53-foot trailer, the standard US dry van.
DEFAULT_LENGTH_FEET = 53.0

#: 80,000 lb gross combination weight -- the federal limit on the Interstate
#: system without a permit (23 CFR 658.17).
DEFAULT_WEIGHT_POUNDS = 80_000.0

#: Five axles: three on the tractor, two on the trailer. With 80,000 lb spread
#: across them the governing figure is the 34,000 lb tandem limit, which works
#: out near 17,000 lb on the heaviest single axle.
DEFAULT_AXLES = 5
DEFAULT_AXLE_LOAD_POUNDS = 17_000.0

FEET_PER_METRE = 3.280839895
POUNDS_PER_TONNE = 2204.622622


@dataclass(frozen=True)
class TruckSpec:
    """Dimensions and weights, in the units a US driver would state them."""

    height_feet: float = DEFAULT_HEIGHT_FEET
    width_feet: float = DEFAULT_WIDTH_FEET
    length_feet: float = DEFAULT_LENGTH_FEET
    weight_pounds: float = DEFAULT_WEIGHT_POUNDS
    axle_load_pounds: float = DEFAULT_AXLE_LOAD_POUNDS
    axles: int = DEFAULT_AXLES
    hazmat: bool = False

    def as_ors_restrictions(self) -> dict:
        """The same vehicle in the units OpenRouteService asks for.

        Per the ORS routing-options reference: length, width and height in
        metres; weight and axleload in *tonnes*, not kilograms. Getting weight
        wrong by a factor of a thousand would silently route a 36-tonne
        combination as though it weighed 36 kg, which is exactly the class of
        bug that produces a plausible-looking route over a weight-limited
        bridge.
        """
        return {
            "height": round(self.height_feet / FEET_PER_METRE, 2),
            "width": round(self.width_feet / FEET_PER_METRE, 2),
            "length": round(self.length_feet / FEET_PER_METRE, 2),
            "weight": round(self.weight_pounds / POUNDS_PER_TONNE, 2),
            "axleload": round(self.axle_load_pounds / POUNDS_PER_TONNE, 2),
            "hazmat": self.hazmat,
        }

    def describe(self) -> str:
        """One line for the Assumptions panel, in the driver's own units."""
        feet = int(self.height_feet)
        inches = round((self.height_feet - feet) * 12)
        return (
            f"{int(self.length_feet)}ft trailer, {feet}'{inches}\" high, "
            f"{self.weight_pounds:,.0f} lb, {self.axles} axles"
            f"{', hazmat' if self.hazmat else ''}"
        )


#: The vehicle every trip is planned for unless something says otherwise.
STANDARD_DRY_VAN = TruckSpec()
