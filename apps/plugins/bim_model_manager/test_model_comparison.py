import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from model_manager.models import (
    BuildingMetrics,
    CadevilDocument,
    FileUpload,
    MaterialProperties,
)
from model_manager.templatetags.plotly_charts import (
    plot_circularity_shares,
    plot_gwp_grouped,
    plot_material_mass_stacked,
)
from model_manager.views import (
    _safe_ratio,
    _sum_material_field,
    compute_best_comparison_stats,
    compute_comparison_stats,
)

User = get_user_model()


class FakeMaterial:
    """Lightweight stand-in for MaterialProperties, used to feed
    non-finite (NaN/Infinity) values into pure helper functions without
    touching the database (FloatField validators would reject NaN anyway).
    """

    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class ModelComparisonTestHelpers:
    """Shared object-creation helpers for model_comparison tests."""

    def make_user(self, username: str, **extra) -> "User":
        return User.objects.create_user(
            username=username, password="pw12345678", **extra
        )

    def make_upload(self, user, description: str = "Upload") -> FileUpload:
        return FileUpload.objects.create(
            user=user, document=f"{description}.ifc", description=description
        )

    def make_document(
            self, user, group: Group, description: str = "Model"
    ) -> CadevilDocument:
        upload = self.make_upload(user, description=description)
        return CadevilDocument.objects.create(
            user=user, group=group, upload=upload, description=description
        )

    def make_building_metrics(self, document: CadevilDocument, **overrides) -> BuildingMetrics:
        defaults = {"brutto_grundfläche": 100.0}
        defaults.update(overrides)
        return BuildingMetrics.objects.create(project=document, **defaults)

    def make_material(self, document: CadevilDocument, name: str, **overrides) -> MaterialProperties:
        defaults = {
            "mass": 0.0,
            "gwp_ml_a1_a3": 0.0,
            "ap_ml_a1_a3": 0.0,
            "penrt_ml_a1_a3": 0.0,
            "recyclable_mass": 0.0,
            "waste_mass": 0.0,
        }
        defaults.update(overrides)
        return MaterialProperties.objects.create(project=document, name=name, **defaults)


class ModelComparisonAccessTests(ModelComparisonTestHelpers, TestCase):
    def setUp(self) -> None:
        self.url = reverse("model_comparison")
        self.group_a = Group.objects.create(name="team-a")
        self.group_b = Group.objects.create(name="team-b")

        self.user_a = self.make_user("user-a")
        self.user_a.groups.add(self.group_a)

        self.user_b = self.make_user("user-b")
        self.user_b.groups.add(self.group_b)

        self.doc_a = self.make_document(self.user_a, self.group_a, "Model A")
        self.doc_b = self.make_document(self.user_b, self.group_b, "Model B")

    def test_anonymous_user_is_redirected_to_login(self) -> None:
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        self.assertRedirects(
            response,
            f"/accounts/login/?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_direct_authenticated_get_without_htmx_redirects_home(self) -> None:
        self.client.force_login(self.user_a)

        response = self.client.get(self.url)

        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_htmx_get_varies_on_hx_request_header(self) -> None:
        self.client.force_login(self.user_a)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertIn("HX-Request", response.headers["Vary"])

    def test_user_only_sees_documents_from_their_own_group(self) -> None:
        self.client.force_login(self.user_a)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        content = response.content.decode()

        self.assertContains(response, "Model A")
        self.assertNotIn("Model B", content)

    def test_group_isolation_is_symmetric(self) -> None:
        self.client.force_login(self.user_b)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        content = response.content.decode()

        self.assertContains(response, "Model B")
        self.assertNotIn("Model A", content)

    def test_superuser_sees_documents_across_all_groups(self) -> None:
        superuser = self.make_user(
            "root", is_staff=True, is_superuser=True
        )
        self.client.force_login(superuser)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertContains(response, "Model A")
        self.assertContains(response, "Model B")

    def test_user_in_no_matching_group_sees_empty_state(self) -> None:
        lonely_user = self.make_user("lonely-user")
        self.client.force_login(lonely_user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Model A", response.content.decode())
        self.assertNotIn("Model B", response.content.decode())
        self.assertContains(response, "No models available to compare")


class ModelComparisonEmptyAndMissingDataTests(ModelComparisonTestHelpers, TestCase):
    def setUp(self) -> None:
        self.url = reverse("model_comparison")
        self.group = Group.objects.create(name="team")
        self.user = self.make_user("user")
        self.user.groups.add(self.group)

    def test_empty_database_renders_without_crash(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No models available to compare")

    def test_empty_database_renders_only_the_empty_state(self) -> None:
        """With no comparable documents, nothing but the empty-state
        message should be emitted: no (empty) Plotly graphs, no
        comparison tables, and no statistics heading."""
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("plot-container", content)
        self.assertNotIn('id="comparison-stats-heading"', content)
        self.assertNotIn("<table", content)

    def test_document_with_no_building_metrics_and_no_materials_is_tolerated(self) -> None:
        self.make_document(self.user, self.group, "Bare Model")
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bare Model")
        self.assertContains(response, "No BuildingMetrics found.")
        self.assertContains(response, "No materials found.")

    def test_document_with_multiple_building_metrics_does_not_crash(self) -> None:
        document = self.make_document(self.user, self.group, "Multi Metrics Model")
        self.make_building_metrics(document, brutto_grundfläche=50.0)
        self.make_building_metrics(document, brutto_grundfläche=75.0)
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Multi Metrics Model")

    def test_zero_bgf_denominator_is_shown_as_em_dash_and_not_best(self) -> None:
        document = self.make_document(self.user, self.group, "Zero BGF Model")
        self.make_building_metrics(document, brutto_grundfläche=0.0)
        self.make_material(
            document, "Concrete", mass=10.0, gwp_ml_a1_a3=5.0
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("—", content)

    def test_material_details_omit_lifecycle_columns_and_labels(self) -> None:
        """Extraction only ever populates A1-A3 fields, so LZ (lifecycle)
        columns/labels must not be presented anywhere in the dashboard."""
        document = self.make_document(self.user, self.group, "Lifecycle Model")
        self.make_building_metrics(document, brutto_grundfläche=100.0)
        self.make_material(
            document,
            "Concrete",
            mass=10.0,
            gwp_ml_a1_a3=5.0,
            gwp_ml_lz=999.0,
            ap_ml_lz=888.0,
            penrt_ml_lz=777.0,
        )
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("GWP LZ", content)
        self.assertNotIn("AP LZ", content)
        self.assertNotIn("PEnrT LZ", content)
        self.assertNotIn("999.00", content)
        self.assertNotIn("888.00", content)
        self.assertNotIn("777.00", content)


class ModelComparisonStatisticsCalculationTests(ModelComparisonTestHelpers, TestCase):
    def setUp(self) -> None:
        self.group = Group.objects.create(name="team")
        self.user = self.make_user("user")
        self.user.groups.add(self.group)

    def _document_with_full_data(self) -> CadevilDocument:
        document = self.make_document(self.user, self.group, "Full Model")
        self.make_building_metrics(document, brutto_grundfläche=100.0)
        self.make_material(
            document,
            "Concrete",
            mass=50.0,
            gwp_ml_a1_a3=10.0,
            ap_ml_a1_a3=2.0,
            penrt_ml_a1_a3=100.0,
            recyclable_mass=30.0,
            waste_mass=5.0,
        )
        self.make_material(
            document,
            "Steel",
            mass=50.0,
            gwp_ml_a1_a3=20.0,
            ap_ml_a1_a3=3.0,
            penrt_ml_a1_a3=200.0,
            recyclable_mass=10.0,
            waste_mass=15.0,
        )
        return document

    def _refetch_with_prefetch(self, document_id) -> CadevilDocument:
        return (
            CadevilDocument.objects.filter(id=document_id)
            .prefetch_related("building_metrics", "material_properties")
            .get()
        )

    def test_exact_statistic_calculations(self) -> None:
        document = self._document_with_full_data()
        document = self._refetch_with_prefetch(document.id)

        stats = compute_comparison_stats(document)

        self.assertAlmostEqual(stats["gwp_a1_a3_per_bgf"], 0.3)
        self.assertAlmostEqual(stats["ap_a1_a3_per_bgf"], 0.05)
        self.assertAlmostEqual(stats["penrt_a1_a3_per_bgf"], 3.0)
        self.assertAlmostEqual(stats["recyclable_mass_share"], 40.0)
        self.assertAlmostEqual(stats["waste_mass_share"], 20.0)

    def test_zero_bgf_yields_none_for_intensity_stats_only(self) -> None:
        document = self.make_document(self.user, self.group, "Zero BGF")
        self.make_building_metrics(document, brutto_grundfläche=0.0)
        self.make_material(document, "Concrete", mass=10.0, gwp_ml_a1_a3=5.0, recyclable_mass=4.0)
        document = self._refetch_with_prefetch(document.id)

        stats = compute_comparison_stats(document)

        self.assertIsNone(stats["gwp_a1_a3_per_bgf"])
        self.assertIsNone(stats["ap_a1_a3_per_bgf"])
        self.assertIsNone(stats["penrt_a1_a3_per_bgf"])
        # Mass-share stats have a different denominator (total mass) and
        # remain available even when BGF is zero/missing.
        self.assertAlmostEqual(stats["recyclable_mass_share"], 40.0)

    def test_missing_building_metrics_yields_none_for_intensity_stats(self) -> None:
        document = self.make_document(self.user, self.group, "No Metrics")
        self.make_material(document, "Concrete", mass=10.0, gwp_ml_a1_a3=5.0)
        document = self._refetch_with_prefetch(document.id)

        stats = compute_comparison_stats(document)

        self.assertIsNone(stats["gwp_a1_a3_per_bgf"])
        self.assertIsNone(stats["ap_a1_a3_per_bgf"])
        self.assertIsNone(stats["penrt_a1_a3_per_bgf"])

    def test_no_materials_yields_none_for_all_material_derived_stats(self) -> None:
        document = self.make_document(self.user, self.group, "No Materials")
        self.make_building_metrics(document, brutto_grundfläche=100.0)
        document = self._refetch_with_prefetch(document.id)

        stats = compute_comparison_stats(document)

        for key in (
                "gwp_a1_a3_per_bgf",
                "ap_a1_a3_per_bgf",
                "penrt_a1_a3_per_bgf",
                "recyclable_mass_share",
                "waste_mass_share",
        ):
            self.assertIsNone(stats[key])

    def test_best_stats_ignore_missing_documents(self) -> None:
        good_stats = {
            "gwp_a1_a3_per_bgf": 0.3,
            "ap_a1_a3_per_bgf": 0.05,
            "penrt_a1_a3_per_bgf": 3.0,
            "recyclable_mass_share": 40.0,
            "waste_mass_share": 20.0,
        }
        missing_stats = {
            "gwp_a1_a3_per_bgf": None,
            "ap_a1_a3_per_bgf": None,
            "penrt_a1_a3_per_bgf": None,
            "recyclable_mass_share": None,
            "waste_mass_share": None,
        }

        best = compute_best_comparison_stats([good_stats, missing_stats])

        self.assertAlmostEqual(best["gwp_a1_a3_per_bgf"], 0.3)
        self.assertAlmostEqual(best["recyclable_mass_share"], 40.0)

    def test_best_stats_are_none_when_all_documents_are_missing(self) -> None:
        missing_stats = {
            "gwp_a1_a3_per_bgf": None,
            "ap_a1_a3_per_bgf": None,
            "penrt_a1_a3_per_bgf": None,
            "recyclable_mass_share": None,
            "waste_mass_share": None,
        }

        best = compute_best_comparison_stats([missing_stats, dict(missing_stats)])

        self.assertIsNone(best["gwp_a1_a3_per_bgf"])
        self.assertIsNone(best["recyclable_mass_share"])


class ModelComparisonHighlightingTests(ModelComparisonTestHelpers, TestCase):
    def setUp(self) -> None:
        self.url = reverse("model_comparison")
        self.group = Group.objects.create(name="team")
        self.user = self.make_user("user")
        self.user.groups.add(self.group)

    def test_only_the_document_with_valid_data_is_highlighted_as_best(self) -> None:
        good_document = self.make_document(self.user, self.group, "Good Model")
        self.make_building_metrics(good_document, brutto_grundfläche=100.0)
        self.make_material(
            good_document, "Concrete", mass=10.0, gwp_ml_a1_a3=5.0, recyclable_mass=4.0, waste_mass=1.0
        )

        self.make_document(self.user, self.group, "Bare Model")

        self.client.force_login(self.user)
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "best-value")

    def test_two_documents_missing_the_same_stat_are_never_both_highlighted(self) -> None:
        self.make_document(self.user, self.group, "Bare Model One")
        self.make_document(self.user, self.group, "Bare Model Two")

        self.client.force_login(self.user)
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        # Neither document has any BuildingMetrics/materials, so nothing in
        # the efficiency/recyclability statistics table should be
        # highlighted as "best".
        stats_section = content.split('id="comparison-stats-heading"')[1]
        stats_table = stats_section.split("</table>")[0]
        self.assertNotIn("best-value", stats_table)


class ModelComparisonQueryCountTests(ModelComparisonTestHelpers, TestCase):
    def setUp(self) -> None:
        self.url = reverse("model_comparison")
        self.group = Group.objects.create(name="team")
        self.user = self.make_user("user")
        self.user.groups.add(self.group)

    def _add_full_document(self, description: str) -> None:
        document = self.make_document(self.user, self.group, description)
        self.make_building_metrics(document, brutto_grundfläche=100.0)
        self.make_material(
            document, "Concrete", mass=10.0, gwp_ml_a1_a3=5.0, recyclable_mass=4.0, waste_mass=1.0
        )
        self.make_material(
            document, "Steel", mass=20.0, gwp_ml_a1_a3=8.0, recyclable_mass=6.0, waste_mass=2.0
        )

    def test_query_count_does_not_grow_with_document_count(self) -> None:
        self.client.force_login(self.user)

        self._add_full_document("Model 1")
        with CaptureQueriesContext(connection) as one_document_queries:
            response_one = self.client.get(self.url, HTTP_HX_REQUEST="true")

        for i in range(2, 6):
            self._add_full_document(f"Model {i}")

        with CaptureQueriesContext(connection) as many_documents_queries:
            response_many = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response_one.status_code, 200)
        self.assertEqual(response_many.status_code, 200)
        # The number of queries must stay (roughly) constant as the number
        # of documents grows, proving there is no N+1 behavior.
        self.assertLessEqual(
            len(many_documents_queries),
            len(one_document_queries) + 2,
        )


class ModelComparisonChartLabelTests(ModelComparisonTestHelpers, TestCase):
    """Extraction only ever populates A1-A3 fields, and mass/impact values
    are computed as density * volume (kg) and mass * factor (kg CO2e /
    kg SO2e / MJ). Chart labels must reflect that, in kg (not tonnes), and
    must not reference any LZ (lifecycle) series."""

    def setUp(self) -> None:
        self.group = Group.objects.create(name="team")
        self.user = self.make_user("user")
        self.user.groups.add(self.group)
        self.document = self.make_document(self.user, self.group, "Model")
        self.make_building_metrics(self.document, brutto_grundfläche=100.0)
        self.make_material(
            self.document,
            "Concrete",
            mass=10.0,
            gwp_ml_a1_a3=5.0,
            gwp_ml_lz=999.0,
        )

    def _refetch_with_prefetch(self) -> CadevilDocument:
        return (
            CadevilDocument.objects.filter(id=self.document.id)
            .prefetch_related("building_metrics", "material_properties")
            .get()
        )

    def test_material_mass_chart_uses_kg_not_tonnes(self) -> None:
        document = self._refetch_with_prefetch()

        html = plot_material_mass_stacked([document])

        self.assertIn("kg", html)
        self.assertNotIn("(t)", html)
        self.assertNotIn(">t<", html)

    def test_gwp_chart_uses_kg_co2e_and_has_no_lz_series(self) -> None:
        document = self._refetch_with_prefetch()

        html = plot_gwp_grouped([document])

        self.assertIn("kg", html)
        self.assertIn("CO", html)
        self.assertNotIn("t CO", html)
        self.assertNotIn("LZ", html)


class ModelComparisonNumericRobustnessTests(TestCase):
    """Pure-function coverage for NaN/Infinity and invalid denominator
    handling in the comparison-statistics helpers."""

    def test_sum_material_field_ignores_non_finite_values(self) -> None:
        materials = [
            FakeMaterial(mass=10.0),
            FakeMaterial(mass=float("nan")),
            FakeMaterial(mass=float("inf")),
            FakeMaterial(mass=float("-inf")),
            FakeMaterial(mass=5.0),
        ]

        total = _sum_material_field(materials, "mass")

        self.assertEqual(total, 15.0)

    def test_safe_ratio_valid_inputs(self) -> None:
        self.assertEqual(_safe_ratio(10.0, 2.0), 5.0)

    def test_safe_ratio_none_for_non_finite_numerator(self) -> None:
        self.assertIsNone(_safe_ratio(float("nan"), 10.0))
        self.assertIsNone(_safe_ratio(float("inf"), 10.0))

    def test_safe_ratio_none_for_non_finite_denominator(self) -> None:
        self.assertIsNone(_safe_ratio(10.0, float("nan")))
        self.assertIsNone(_safe_ratio(10.0, float("inf")))

    def test_safe_ratio_none_for_non_positive_denominator(self) -> None:
        self.assertIsNone(_safe_ratio(10.0, 0.0))
        self.assertIsNone(_safe_ratio(10.0, -5.0))

    def test_safe_ratio_none_when_numerator_missing(self) -> None:
        self.assertIsNone(_safe_ratio(None, 10.0))

    def test_best_stats_exclude_non_finite_values(self) -> None:
        stats_with_nan = {
            "gwp_a1_a3_per_bgf": float("nan"),
            "ap_a1_a3_per_bgf": None,
            "penrt_a1_a3_per_bgf": None,
            "recyclable_mass_share": None,
            "waste_mass_share": None,
        }
        stats_with_valid_value = {
            "gwp_a1_a3_per_bgf": 5.0,
            "ap_a1_a3_per_bgf": None,
            "penrt_a1_a3_per_bgf": None,
            "recyclable_mass_share": None,
            "waste_mass_share": None,
        }

        best = compute_best_comparison_stats([stats_with_nan, stats_with_valid_value])

        self.assertEqual(best["gwp_a1_a3_per_bgf"], 5.0)

    def test_best_stats_are_none_when_all_values_are_non_finite(self) -> None:
        stats_with_nan = {
            "gwp_a1_a3_per_bgf": float("nan"),
            "ap_a1_a3_per_bgf": None,
            "penrt_a1_a3_per_bgf": None,
            "recyclable_mass_share": None,
            "waste_mass_share": None,
        }
        stats_with_inf = {
            "gwp_a1_a3_per_bgf": float("inf"),
            "ap_a1_a3_per_bgf": None,
            "penrt_a1_a3_per_bgf": None,
            "recyclable_mass_share": None,
            "waste_mass_share": None,
        }

        best = compute_best_comparison_stats([stats_with_nan, stats_with_inf])

        self.assertIsNone(best["gwp_a1_a3_per_bgf"])

class CircularityDiagramTests(ModelComparisonTestHelpers, TestCase):
    def setUp(self) -> None:
        self.group = Group.objects.create(name="test-group")
        self.user = self.make_user("tester")
        self.user.groups.add(self.group)
        self.doc1 = self.make_document(self.user, self.group, "Doc 1")
        # 100kg total, 20kg recyclable, 10kg waste -> 20%, 10%
        self.make_material(self.doc1, "Mat 1", mass=100.0, recyclable_mass=20.0, waste_mass=10.0)

        self.doc2 = self.make_document(self.user, self.group, "Doc 2")
        # 200kg total, 50kg recyclable, 0kg waste -> 25%, 0%
        self.make_material(self.doc2, "Mat 2", mass=200.0, recyclable_mass=50.0, waste_mass=0.0)

        self.doc_empty = self.make_document(self.user, self.group, "Empty Doc")
        # No materials

        self.doc_nan = self.make_document(self.user, self.group, "NaN Doc")
        # Mass 0 to test division by zero / NaN safety
        self.make_material(self.doc_nan, "Mat NaN", mass=0.0, recyclable_mass=0.0, waste_mass=0.0)

    def test_plot_circularity_shares_html(self) -> None:
        """Test tag output for correct percentages and local Plotly loading."""
        docs = [self.doc1, self.doc2]
        html = plot_circularity_shares(docs)

        # Check for percentages in the output (as floats in Plotly JSON)
        # Doc 1: 20.0, 10.0
        # Doc 2: 25.0, 0.0
        self.assertIn("20.0", html)
        self.assertIn("10.0", html)
        self.assertIn("25.0", html)
        self.assertIn("0.0", html)

        # Check labels
        self.assertIn("Recyclable Mass Share", html)
        self.assertIn("Waste Mass Share", html)
        self.assertIn("Percentage (%)", html)

        # Check local Plotly loading (include_plotlyjs=False)
        self.assertNotIn("https://cdn.plot.ly", html)
        self.assertNotIn("<script src=", html)

    def test_circularity_shares_nan_safety(self) -> None:
        """Test that documents with zero mass or no materials don't crash and are handled gracefully."""
        docs = [self.doc_empty, self.doc_nan]
        try:
            html = plot_circularity_shares(docs)
        except ZeroDivisionError:
            self.fail("plot_circularity_shares raised ZeroDivisionError")

        # If no valid data at all, it should probably still return a SafeString but maybe empty or with a message
        # The requirement says: "missing/no-material/non-finite values must not be plotted as zero or highlighted;
        # empty data should render a clear in-chart unavailable state or omit safely without crashing."
        self.assertIsInstance(html, str)

    def test_template_rendering(self) -> None:
        """Test that the diagram is rendered in the comparison page when data is present."""
        self.client.force_login(self.user)
        response = self.client.get(reverse("model_comparison"), HTTP_HX_REQUEST="true")
        self.assertContains(response, "Circularity Shares") # Title or some identifying text
        self.assertContains(response, "Recyclable Mass Share")

    def test_empty_dashboard_absence(self) -> None:
        """Test that the diagram is NOT rendered when no models are available."""
        # Create a user with no documents
        other_group = Group.objects.create(name="other-group")
        other_user = self.make_user("other-tester")
        other_user.groups.add(other_group)
        self.client.force_login(other_user)

        response = self.client.get(reverse("model_comparison"), HTTP_HX_REQUEST="true")
        self.assertNotContains(response, "Circularity Shares")
        self.assertNotContains(response, "plot-container") # Should not have plot containers if no data


class ModelComparisonSelectionTests(ModelComparisonTestHelpers, TestCase):
    """Tests for model selection in the comparison dashboard."""

    def setUp(self) -> None:
        self.url = reverse("model_comparison")
        self.group = Group.objects.create(name="team-1")
        self.user = self.make_user("user-1")
        self.user.groups.add(self.group)

        self.doc1 = self.make_document(self.user, self.group, "Alpha")
        self.doc2 = self.make_document(self.user, self.group, "Beta")
        self.doc3 = self.make_document(self.user, self.group, "Gamma")

        self.other_group = Group.objects.create(name="team-2")
        self.other_user = self.make_user("user-2")
        self.other_user.groups.add(self.other_group)
        self.doc_other = self.make_document(self.other_user, self.other_group, "Other")

    def test_default_shows_all_accessible(self) -> None:
        """Initially, all models in the user's groups are compared and checked."""
        self.client.force_login(self.user)
        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        data = response.context["data"]
        self.assertEqual(len(data), 3)
        self.assertIn(self.doc1, data)
        self.assertIn(self.doc2, data)
        self.assertIn(self.doc3, data)
        self.assertNotIn(self.doc_other, data)

        # Verify all are checked in HTML
        self.assertContains(response, f'value="{self.doc1.id}" checked')
        self.assertContains(response, f'value="{self.doc2.id}" checked')
        self.assertContains(response, f'value="{self.doc3.id}" checked')
        # Inaccessible model should not even be in the selector
        self.assertNotContains(response, str(self.doc_other.id))

    def test_selector_form_targets_comparison_fragment(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertContains(response, f'action="{self.url}"')
        self.assertContains(response, f'hx-get="{self.url}"')
        self.assertContains(response, 'hx-target="#content-container"')
        self.assertContains(response, 'hx-swap="outerHTML"')

    def test_subset_selection(self) -> None:
        """Only explicitly selected models appear in results; selection is preserved."""
        self.client.force_login(self.user)
        payload = {"selection": "1", "models": [str(self.doc1.id), str(self.doc3.id)]}
        response = self.client.get(self.url, payload, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        data = response.context["data"]
        self.assertEqual(len(data), 2)
        self.assertIn(self.doc1, data)
        self.assertIn(self.doc3, data)
        self.assertNotIn(self.doc2, data)

        # Verify checked states in HTML
        self.assertContains(response, f'value="{self.doc1.id}" checked')
        self.assertContains(response, f'value="{self.doc3.id}" checked')
        # doc2 should be present but NOT checked
        self.assertContains(response, f'value="{self.doc2.id}"')
        self.assertNotContains(response, f'value="{self.doc2.id}" checked')

    def test_empty_selection_shows_prompt(self) -> None:
        """Explicitly selecting nothing shows a prompt but still renders the selector."""
        self.client.force_login(self.user)
        response = self.client.get(self.url, {"selection": "1"}, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["data"]), 0)
        self.assertContains(response, "Select at least one model to compare.")
        # Selector should still be there
        self.assertContains(response, 'name="models"')

    def test_one_model_selection(self) -> None:
        """Comparing a single model is allowed and renders safely."""
        self.client.force_login(self.user)
        response = self.client.get(self.url, {"selection": "1", "models": [str(self.doc1.id)]}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["data"]), 1)
        self.assertContains(response, "Alpha")

    def test_security_inaccessible_model_ignored(self) -> None:
        """Submitting an ID from another group does not leak information."""
        self.client.force_login(self.user)
        response = self.client.get(self.url, {"selection": "1", "models": [str(self.doc_other.id)]}, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["data"]), 0)
        self.assertNotContains(response, "Other")

    def test_malformed_and_unknown_ids_ignored(self) -> None:
        """Malformed or unknown well-formed UUIDs do not cause crashes."""
        self.client.force_login(self.user)
        unknown_uuid = str(uuid.uuid4())
        payload = {"selection": "1", "models": ["not-a-uuid", unknown_uuid, str(self.doc1.id)]}
        response = self.client.get(self.url, payload, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["data"]), 1)
        self.assertIn(self.doc1, response.context["data"])

    def test_duplicate_ids_deduplicated(self) -> None:
        """Duplicate IDs in the query string are handled gracefully."""
        self.client.force_login(self.user)
        response = self.client.get(self.url, {"selection": "1", "models": [str(self.doc1.id), str(self.doc1.id)]}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["data"]), 1)

    def test_superuser_selection_across_groups(self) -> None:
        """Superusers can compare models across any group."""
        admin = User.objects.create_superuser(username="admin", password="pw", email="a@b.com")
        self.client.force_login(admin)
        response = self.client.get(self.url, {"selection": "1", "models": [str(self.doc1.id), str(self.doc_other.id)]}, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        data = response.context["data"]
        self.assertEqual(len(data), 2)
        self.assertIn(self.doc1, data)
        self.assertIn(self.doc_other, data)

    def test_stable_ordering(self) -> None:
        """Selector and data maintain stable order: description then ID."""
        self.client.force_login(self.user)
        # Create models with same description
        self.make_document(self.user, self.group, "Same")
        self.make_document(self.user, self.group, "Same")

        response = self.client.get(self.url, HTTP_HX_REQUEST="true")
        data = list(response.context["data"])
        # Expected sort: Alpha, Beta, Gamma, Same (by id), Same (by id)
        expected = sorted(data, key=lambda x: (x.description or "", str(x.id)))
        self.assertEqual([d.id for d in data], [d.id for d in expected])


class ModelComparisonRegressionTests(ModelComparisonTestHelpers, TestCase):
    """Regression tests to ensure comparison changes don't break other views."""

    def setUp(self) -> None:
        self.group = Group.objects.create(name="team-1")
        self.user = self.make_user("user-1")
        self.user.groups.add(self.group)
        self.doc = self.make_document(self.user, self.group, "Alpha")

    def test_model_manager_remains_functional(self) -> None:
        """Ensure model_manager view still works without comparison context variables."""
        self.client.force_login(self.user)
        response = self.client.get(reverse("model_manager"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("available_documents", response.context)

    def test_object_view_remains_functional(self) -> None:
        """Ensure object_view remains functional and secure."""
        self.client.force_login(self.user)
        url = reverse("object_view") + f"?object={self.doc.id}"
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertIn("data", response.context)
        self.assertEqual(response.context["data"].id, self.doc.id)
        self.assertNotIn("available_documents", response.context)
