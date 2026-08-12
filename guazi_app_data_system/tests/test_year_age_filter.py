import unittest
from datetime import date

from guazi_app_data_system.year_age_filter import (
    calculate_target_age,
    calculate_target_age_from_vehicle_year,
    calculate_left_handle_target_coordinate,
    calculate_right_handle_overlap_target_coordinate,
    calculate_right_handle_target_coordinate,
    choose_narrowest_age_option,
    detect_dual_handle_slider,
    parse_age_range_label,
    parse_age_slider_current_value,
    parse_left_handle_value,
    parse_right_handle_value,
    parse_vehicle_age_options,
    requires_vehicle_year_secondary_check,
    handles_physically_overlap,
    validate_exact_age_range,
    validate_exact_age_value,
)


class YearAgeFilterTest(unittest.TestCase):
    def test_target_age_for_current_task_is_six(self):
        target_age = calculate_target_age("2020.4", 2020, date(2026, 4, 22))

        self.assertEqual(target_age, 6)

    def test_calculate_target_age_from_vehicle_year_keeps_simple_year_diff(self):
        self.assertEqual(calculate_target_age_from_vehicle_year(2020, current_year=2026), 6)

    def test_parse_age_slider_current_value(self):
        self.assertEqual(parse_age_slider_current_value("车龄 6年"), 6)
        self.assertEqual(parse_age_slider_current_value("6年"), 6)
        self.assertIsNone(parse_age_slider_current_value("不限车龄"))

    def test_validate_exact_age_value(self):
        self.assertTrue(validate_exact_age_value("6年", 6))
        self.assertFalse(validate_exact_age_value("5年", 6))

    def test_choose_narrowest_age_option_never_returns_unlimited(self):
        options = parse_vehicle_age_options(
            [
                {"text": "不限车龄", "bounds": [0, 0, 10, 10]},
                {"text": "5-8年", "bounds": [0, 11, 10, 20]},
                {"text": "6年", "bounds": [0, 21, 10, 30]},
            ]
        )
        result = choose_narrowest_age_option(options, 6)

        self.assertEqual(result["recommended_option"]["raw_text"], "6年")
        self.assertTrue(result["requires_vehicle_year_secondary_check"])

    def test_vehicle_year_secondary_check_is_always_required(self):
        self.assertTrue(requires_vehicle_year_secondary_check())

    def test_detect_dual_handle_slider_reads_left_and_right_values(self):
        slider = detect_dual_handle_slider(
            {
                "dual_handle_detected": True,
                "left_handle_bounds": [500, 1180, 660, 1240],
                "right_handle_bounds": [860, 1180, 1020, 1240],
                "left_handle_value": 6,
                "right_handle_value": 10,
                "slider_bounds": [279, 1235, 1192, 1274],
                "track_bounds": [279, 1235, 1192, 1274],
            }
        )

        self.assertTrue(slider["dual_handle_detected"])
        self.assertEqual(parse_left_handle_value(slider), 6)
        self.assertEqual(parse_right_handle_value(slider), 10)

    def test_validate_exact_age_range_requires_both_handles_equal_target(self):
        self.assertTrue(validate_exact_age_range(6, 6, 6))
        self.assertFalse(validate_exact_age_range(6, 10, 6))
        self.assertFalse(validate_exact_age_range(4, 6, 6))
        self.assertFalse(validate_exact_age_range(6, "不限", 6))
        self.assertTrue(
            validate_exact_age_range(
                6,
                6,
                6,
                left_handle_bounds=[650, 1235, 773, 1387],
                right_handle_bounds=[656, 1238, 779, 1388],
                require_physical_overlap=True,
                target_age_calculation_verified=True,
            )
        )
        self.assertFalse(
            validate_exact_age_range(
                6,
                6,
                6,
                left_handle_bounds=[650, 1235, 773, 1387],
                right_handle_bounds=[1053, 1235, 1176, 1387],
                require_physical_overlap=True,
                target_age_calculation_verified=True,
            )
        )

    def test_parse_over_age_label_as_left_target_and_unlimited_right(self):
        parsed = parse_age_range_label("6年以上")

        self.assertEqual(parsed["left_handle_value"], 6)
        self.assertEqual(parsed["right_handle_value"], "不限")

    def test_left_target_right_unlimited_is_not_exact_value(self):
        slider = detect_dual_handle_slider(
            {
                "dual_handle_detected": True,
                "left_handle_bounds": [624, 1186, 789, 1244],
                "right_handle_bounds": [1027, 1196, 1160, 1251],
                "selected_range_label": "6年以上",
            }
        )

        self.assertEqual(parse_left_handle_value(slider), 6)
        self.assertEqual(parse_right_handle_value(slider), "不限")
        self.assertFalse(
            validate_exact_age_range(
                parse_left_handle_value(slider),
                parse_right_handle_value(slider),
                6,
            )
        )

    def test_right_handle_exact_target_uses_left_physical_center_not_tick_text_center(self):
        track_bounds = [279, 1235, 1192, 1274]
        target_coordinate = [706, 1254]
        left_handle_bounds = [650, 1235, 773, 1387]

        self.assertEqual(
            calculate_left_handle_target_coordinate(track_bounds, target_coordinate),
            [706, 1254],
        )
        self.assertEqual(
            calculate_right_handle_target_coordinate(track_bounds, target_coordinate),
            [706, 1254],
        )
        self.assertEqual(calculate_right_handle_overlap_target_coordinate(left_handle_bounds), [711, 1311])
        self.assertNotEqual(calculate_right_handle_overlap_target_coordinate(left_handle_bounds), target_coordinate)

    def test_physical_overlap_requires_handle_centers_to_match(self):
        self.assertTrue(handles_physically_overlap([650, 1235, 773, 1387], [656, 1238, 779, 1388]))
        self.assertFalse(handles_physically_overlap([650, 1235, 773, 1387], [1053, 1235, 1176, 1387]))


if __name__ == "__main__":
    unittest.main()
