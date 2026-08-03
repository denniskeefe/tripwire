"""
Tests for content normalization.

The property that matters: two fetches of a page whose only differences are
volatile (timestamps, counters) must normalize to identical text, while a
fetch containing genuinely new content must not.

Run with:  python3 -m unittest discover tests
"""

import unittest

from scrapers.normalize import normalize, parse_ignore_spec, validate_ignore_spec


class TestVolatileTextIsStable(unittest.TestCase):
    """Noise-only differences must not change the normalized output."""

    def assertStable(self, before, after, msg=None):
        self.assertEqual(normalize(before), normalize(after), msg)

    def test_relative_timestamps(self):
        self.assertStable(
            "Posted 2 hours ago\nSome tweet body",
            "Posted 3 hours ago\nSome tweet body",
        )
        self.assertStable("Updated 5 minutes ago", "Updated 47 minutes ago")
        self.assertStable("Replied just now", "Replied 1 day ago")
        self.assertStable("a few seconds ago", "12 seconds ago")

    def test_compact_relative_timestamps(self):
        self.assertStable(
            "@handle · 2h\nThe actual post text",
            "@handle · 9h\nThe actual post text",
        )

    def test_engagement_counters(self):
        self.assertStable(
            "1,204 likes\n88 comments\nHeadline stays put",
            "1,331 likes\n91 comments\nHeadline stays put",
        )
        self.assertStable("12.4K views", "13.1K views")
        self.assertStable("5,102 followers", "5,109 followers")

    def test_labelled_counter_forms(self):
        self.assertStable("Views: 12.4K", "Views: 13.1K")
        self.assertStable("Followers\n5,102", "Followers\n5,109")
        self.assertStable("Comments 88", "Comments 91")  # label alone on its line

    def test_clock_times_and_dates(self):
        self.assertStable("Last seen 14:32", "Last seen 09:07")
        self.assertStable("Archived 2026-01-04", "Archived 2026-02-19")
        self.assertStable("Filed Mar 3, 2025", "Filed Apr 18, 2025")

    def test_opaque_tokens(self):
        self.assertStable(
            "img?v=a3f9c2b17d4e8f0a9b6c5d4e3f2a1b0c9d8e7f6a",
            "img?v=ffffffff7d4e8f0a9b6c5d4e3f2a1b0c9d8e7f6a",
        )

    def test_whitespace_and_blank_lines(self):
        self.assertStable(
            "Title\n\n\n   Body   text\n",
            "Title\nBody text",
        )

    def test_repeated_nav_lines_folded(self):
        self.assertStable("Home\nHome\nHome\nContent", "Home\nContent")


class TestRealChangesSurvive(unittest.TestCase):
    """Substantive differences must still register."""

    def assertChanged(self, before, after):
        self.assertNotEqual(normalize(before), normalize(after))

    def test_new_post_detected(self):
        self.assertChanged(
            "3 hours ago\nOld post about nothing",
            "3 hours ago\nBrand new post about something\nOld post about nothing",
        )

    def test_edited_text_detected(self):
        self.assertChanged("1 like\nMeeting at the docks", "1 like\nMeeting at the airport")

    def test_numbers_outside_counter_context_survive(self):
        self.assertChanged("Flight 447 confirmed", "Flight 448 confirmed")

    def test_short_hex_not_treated_as_token(self):
        self.assertChanged("case ref ab12cd34", "case ref ef56ab78")

    def test_number_merely_following_a_counter_label_is_kept(self):
        """Regression: "8 comments 2. Title" must not eat the "2."."""
        self.assertChanged(
            "8 comments 2. First headline",
            "8 comments 3. Second headline",
        )
        self.assertChanged("5 comments 2024 in review", "5 comments 2025 in review")


class TestIgnoreSpec(unittest.TestCase):
    def test_custom_pattern_strips_content(self):
        spec = "Sponsored.*"
        a = normalize("Real post\nSponsored: buy this now", spec)
        b = normalize("Real post\nSponsored: buy that instead", spec)
        self.assertEqual(a, b)
        self.assertIn("Real post", a)

    def test_line_anchors_match_lines_not_whole_document(self):
        spec = r"^#\w+ <count> posts$"
        self.assertEqual(
            normalize("Real post\nTrending\n#SomeTag 14.2K posts", spec),
            normalize("Real post\nTrending\n#OtherTag 19.8K posts", spec),
        )

    def test_custom_patterns_run_against_placeholder_form(self):
        # Written against "<count>", not the raw "8.7K".
        self.assertEqual(normalize("8.7K likes", r"<count> likes"), "")

    def test_comments_and_blank_lines_ignored(self):
        patterns, disabled, errors = parse_ignore_spec("# just a note\n\n  \nfoo")
        self.assertEqual(len(patterns), 1)
        self.assertEqual(errors, [])
        self.assertEqual(disabled, set())

    def test_disabling_a_rule_group(self):
        text_a, text_b = "1,204 likes", "1,331 likes"
        self.assertEqual(normalize(text_a), normalize(text_b))
        self.assertNotEqual(
            normalize(text_a, "!engagement"),
            normalize(text_b, "!engagement"),
        )

    def test_disabling_one_group_keeps_the_others(self):
        self.assertEqual(
            normalize("2 hours ago\n5 likes", "!engagement"),
            normalize("9 hours ago\n5 likes", "!engagement"),
        )

    def test_invalid_regex_is_reported_not_raised(self):
        errors = validate_ignore_spec("valid.*\n[unclosed")
        self.assertEqual(len(errors), 1)
        self.assertIn("line 2", errors[0])

    def test_bad_line_does_not_disable_good_lines(self):
        patterns, _, errors = parse_ignore_spec("[unclosed\nSponsored")
        self.assertEqual(len(patterns), 1)
        self.assertEqual(len(errors), 1)

    def test_unknown_rule_group_reported(self):
        errors = validate_ignore_spec("!nosuchgroup")
        self.assertEqual(len(errors), 1)
        self.assertIn("unknown rule group", errors[0])


class TestEdgeCases(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize(None), "")

    def test_whitespace_only_input(self):
        self.assertEqual(normalize("   \n\n \t "), "")

    def test_pattern_that_strips_everything(self):
        self.assertEqual(normalize("anything at all", ".*"), "")


if __name__ == "__main__":
    unittest.main()
