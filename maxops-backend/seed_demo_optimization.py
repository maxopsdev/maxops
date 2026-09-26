"""Backwards-compatible demo seed entrypoint.

This now delegates to the comprehensive seeder so the default demo seed command
creates:
- account/region/settings
- data for every check
- resource data for every check
- historical check data (>= 3 months, >= 3 runs/check)
- potential savings for each check
"""

from seed_comprehensive_demo import seed_comprehensive_demo_data


def seed_demo_data(force: bool = True):
    """Compatibility wrapper used by existing docs and scripts."""
    return seed_comprehensive_demo_data(force=force)


if __name__ == "__main__":
    summary = seed_demo_data(force=True)
    print("Demo data seeded successfully:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
