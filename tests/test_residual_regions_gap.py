import numpy as np

from dara.search.peak_matcher import find_residual_regions


def test_profile_gap_from_an_excluded_range_does_not_crash():
    # BGMN leaves the excluded 60-62 deg range out of the profile.
    x = np.concatenate([np.arange(10.0, 60.0, 0.02), np.arange(62.0, 80.0, 0.02)])
    bkg = np.full_like(x, 100.0)
    peak = 2000.0 * np.exp(-0.5 * ((x - 30.0) / 0.08) ** 2)
    regions = find_residual_regions(x, bkg + peak, bkg + peak, bkg)
    assert regions.shape == (0, 2)
