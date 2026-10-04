import tempfile
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from plugins.bim_model_manager.ifc_extractor.chart_plotter import (
    MaterialNotFoundError,
    plot_material_costs,
    simulate_material_cost_projection_plotly,
    simulate_material_decay_plotly,
)
from shared.models import CadevilUser
from plugins.bim_model_manager.django.models import (
    CadevilDocument,
    FileUpload,
    MaterialProperties,
)

class ChartPlotterTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.user = CadevilUser.objects.create_user(username="testuser", password="password")
        self.group = Group.objects.create(name="testgroup")
        
        self.ifc_file = FileUpload.objects.create(
            user=self.user,
            document=ContentFile(b"fake ifc", name="test.ifc"),
            description="Test IFC"
        )
        
        self.doc = CadevilDocument.objects.create(
            user=self.user,
            group=self.group,
            upload=self.ifc_file,
            description="Test Project"
        )
        
        # Create some materials
        self.mat1 = MaterialProperties.objects.create(
            project=self.doc,
            name="Steel",
            mass=1000.0,
            global_brutto_price=90.0,
            local_brutto_price=100.0,
            local_netto_price=80.0,
        )
        self.mat2 = MaterialProperties.objects.create(
            project=self.doc,
            name="Concrete",
            mass=5000.0,
            global_brutto_price=450.0,
            local_brutto_price=500.0,
            local_netto_price=400.0,
        )
        self.mat_no_mass = MaterialProperties.objects.create(
            project=self.doc,
            name="Air",
            mass=0.0
        )
        
        self.config = {
            "Steel": {
                "Nutzungsdauer": 50,
                "Abfallreduktion": 10,
                "Neu Abfallreduktion": 5,
                "Recycling": 80,
                "Neu Recycling": 90
            },
            "Concrete": {
                "Nutzungsdauer": 100,
                "Abfallreduktion": 50,
                "Neu Abfallreduktion": 20,
                "Recycling": 10,
                "Neu Recycling": 5
            }
        }

    def test_default_selection_highest_mass(self):
        # Concrete has 5000kg, Steel has 1000kg. Concrete should be picked.
        html = simulate_material_decay_plotly(self.doc, self.config, years=100)
        self.assertIn("Concrete", html)
        self.assertIn(f"decay_simulation_{str(self.mat2.id).replace('-', '_')}", html)

    def test_material_names_cannot_close_chart_scripts_or_create_html_nodes(self):
        from html.parser import HTMLParser

        class Nodes(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []
                self.script_ends = 0

            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)

            def handle_endtag(self, tag):
                if tag == "script":
                    self.script_ends += 1

        hostile = '</script><img src=x onerror="alert(1)">&\u2028\u2029'
        self.mat1.name = hostile
        self.mat1.save(update_fields=["name"])
        config = {hostile: self.config["Steel"]}
        outputs = [plot_material_costs(self.doc),
                   simulate_material_decay_plotly(self.doc, config, material_id=self.mat1.pk),
                   simulate_material_cost_projection_plotly(self.doc, config, material_id=self.mat1.pk)]
        for output in outputs:
            with self.subTest(chart=output[:100]):
                parser = Nodes()
                parser.feed(output)
                self.assertNotIn("img", parser.tags)
                self.assertNotIn(hostile, output)
                self.assertEqual(parser.tags.count("script"), parser.script_ends)

    def test_select_by_id(self):
        # Explicitly select Steel
        html = simulate_material_decay_plotly(self.doc, self.config, years=100, material_id=self.mat1.id)
        self.assertIn("Steel", html)
        self.assertIn(f"decay_simulation_{str(self.mat1.id).replace('-', '_')}", html)

    def test_foreign_material_id_raises_error(self):
        other_doc = CadevilDocument.objects.create(
            user=self.user, 
            group=self.group,
            upload=self.ifc_file
        )
        other_mat = MaterialProperties.objects.create(project=other_doc, name="Other", mass=10.0)
        
        with self.assertRaises(MaterialNotFoundError):
            simulate_material_decay_plotly(self.doc, self.config, material_id=other_mat.id)

    def test_unknown_material_id_raises_error(self):
        with self.assertRaises(MaterialNotFoundError):
            simulate_material_decay_plotly(self.doc, self.config, material_id=uuid4())

    def test_empty_project_returns_unavailable(self):
        empty_doc = CadevilDocument.objects.create(
            user=self.user, 
            group=self.group,
            upload=self.ifc_file
        )
        html = simulate_material_decay_plotly(empty_doc, self.config)
        self.assertIn("unavailable", html.lower())
        self.assertIn("No materials", html)

    def test_missing_config_returns_unavailable(self):
        # Material "Air" has no config
        html = simulate_material_decay_plotly(self.doc, self.config, material_id=self.mat_no_mass.id)
        self.assertIn("unavailable", html.lower())
        self.assertIn("Configuration missing", html)

    def test_invalid_service_life_returns_unavailable(self):
        bad_config = {"Steel": {"Nutzungsdauer": 0}}
        html = simulate_material_decay_plotly(self.doc, bad_config, material_id=self.mat1.id)
        self.assertIn("unavailable", html.lower())
        self.assertIn("Invalid service life", html)

    def test_invalid_years_raises_value_error(self):
        with self.assertRaises(ValueError):
            simulate_material_decay_plotly(self.doc, self.config, years=0)
        with self.assertRaises(ValueError):
            simulate_material_decay_plotly(self.doc, self.config, years=-10)

    def test_clamped_rates_and_finite_output(self):
        # Test with rates > 100%
        crazy_config = {
            "Steel": {
                "Nutzungsdauer": 50,
                "Abfallreduktion": 200, # Should clamp to 100%
                "Neu Abfallreduktion": -50, # Should clamp to 0%
                "Recycling": 150,
                "Neu Recycling": 100
            }
        }
        html = simulate_material_decay_plotly(self.doc, crazy_config, material_id=self.mat1.id)
        self.assertIn("Steel", html)
        # Verify it didn't crash and produced a chart
        self.assertIn("plotly-graph", html)

    def test_plotly_labels_english(self):
        html = simulate_material_decay_plotly(self.doc, self.config, material_id=self.mat1.id)
        self.assertIn("Time (Years)", html)
        self.assertIn("Material Remaining (%)", html)
        self.assertIn("Material Recycling Simulation", html)


class CostProjectionTests(TestCase):
    """Tests for ``simulate_material_cost_projection_plotly``, mirroring the
    ``simulate_material_decay_plotly`` coverage above since both share the
    same material-selection/config-validation helpers."""

    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.user = CadevilUser.objects.create_user(username="costuser", password="password")
        self.group = Group.objects.create(name="costgroup")

        self.ifc_file = FileUpload.objects.create(
            user=self.user,
            document=ContentFile(b"fake ifc", name="test.ifc"),
            description="Test IFC"
        )

        self.doc = CadevilDocument.objects.create(
            user=self.user,
            group=self.group,
            upload=self.ifc_file,
            description="Test Project"
        )

        self.mat1 = MaterialProperties.objects.create(
            project=self.doc,
            name="Steel",
            mass=1000.0,
            global_brutto_price=90.0,
            local_brutto_price=100.0,
            local_netto_price=80.0,
        )
        self.mat2 = MaterialProperties.objects.create(
            project=self.doc,
            name="Concrete",
            mass=5000.0,
            global_brutto_price=450.0,
            local_brutto_price=500.0,
            local_netto_price=400.0,
        )
        self.mat_no_mass = MaterialProperties.objects.create(
            project=self.doc,
            name="Air",
            mass=0.0
        )

        self.config = {
            "Steel": {"Nutzungsdauer": 50},
            "Concrete": {"Nutzungsdauer": 100},
        }

    @staticmethod
    def _cost_series(*args, **kwargs) -> list[float]:
        """Run the simulation but return the underlying ``y`` values of the
        resulting Figure instead of parsing the rendered HTML - Plotly
        serializes numeric arrays as base64-encoded binary data rather than
        plain, human-readable JSON numbers, so exact totals can't reliably be
        asserted against the HTML string itself.
        """
        captured = {}

        def fake_to_html(fig, *_args, **_kwargs):
            captured["fig"] = fig
            return "<div>stub</div>"

        with patch("plugins.bim_model_manager.ifc_extractor.chart_plotter.pio.to_html", side_effect=fake_to_html):
            simulate_material_cost_projection_plotly(*args, **kwargs)

        return list(captured["fig"].data[0].y)

    def test_default_selection_highest_mass(self):
        html = simulate_material_cost_projection_plotly(self.doc, self.config, years=100)
        self.assertIn("Concrete", html)
        self.assertIn(f"cost_projection_{str(self.mat2.id).replace('-', '_')}", html)

    def test_select_by_id(self):
        html = simulate_material_cost_projection_plotly(
            self.doc, self.config, years=100, material_id=self.mat1.id
        )
        self.assertIn("Steel", html)
        self.assertIn(f"cost_projection_{str(self.mat1.id).replace('-', '_')}", html)

    def test_foreign_material_id_raises_error(self):
        other_doc = CadevilDocument.objects.create(
            user=self.user,
            group=self.group,
            upload=self.ifc_file
        )
        other_mat = MaterialProperties.objects.create(project=other_doc, name="Other", mass=10.0)

        with self.assertRaises(MaterialNotFoundError):
            simulate_material_cost_projection_plotly(self.doc, self.config, material_id=other_mat.id)

    def test_unknown_material_id_raises_error(self):
        with self.assertRaises(MaterialNotFoundError):
            simulate_material_cost_projection_plotly(self.doc, self.config, material_id=uuid4())

    def test_empty_project_returns_unavailable(self):
        empty_doc = CadevilDocument.objects.create(
            user=self.user,
            group=self.group,
            upload=self.ifc_file
        )
        html = simulate_material_cost_projection_plotly(empty_doc, self.config)
        self.assertIn("unavailable", html.lower())
        self.assertIn("No materials", html)

    def test_missing_config_returns_unavailable(self):
        html = simulate_material_cost_projection_plotly(
            self.doc, self.config, material_id=self.mat_no_mass.id
        )
        self.assertIn("unavailable", html.lower())
        self.assertIn("Configuration missing", html)

    def test_invalid_service_life_returns_unavailable(self):
        bad_config = {"Steel": {"Nutzungsdauer": 0}}
        html = simulate_material_cost_projection_plotly(self.doc, bad_config, material_id=self.mat1.id)
        self.assertIn("unavailable", html.lower())
        self.assertIn("Invalid service life", html)

    def test_invalid_years_raises_value_error(self):
        with self.assertRaises(ValueError):
            simulate_material_cost_projection_plotly(self.doc, self.config, years=0)
        with self.assertRaises(ValueError):
            simulate_material_cost_projection_plotly(self.doc, self.config, years=-10)

    def test_defaults_to_no_escalation_when_missing(self):
        # "Preissteigerung" isn't in the config at all - a single replacement
        # at year 50 (Steel's service life) should cost exactly its base
        # price again (100), so cumulative goes from 100 to 200.
        values = self._cost_series(
            self.doc, self.config, years=50, material_id=self.mat1.id
        )
        self.assertEqual(values[0], 100.0)
        self.assertEqual(values[-1], 200.0)

    def test_rises_with_replacement_and_escalation(self):
        material = MaterialProperties.objects.create(
            project=self.doc, name="Copper", mass=1.0, local_brutto_price=1000.0
        )
        config = {
            "Copper": {"Nutzungsdauer": 1, "Preissteigerung": 100},  # 100%/year
        }
        values = self._cost_series(
            self.doc, config, years=2, material_id=material.id
        )
        # Year 0: 1000 (initial installation cost).
        # Year 1 replacement: 1000 * 2**1 = 2000 -> cumulative 3000.
        # Year 2 replacement: 1000 * 2**2 = 4000 -> cumulative 7000.
        self.assertEqual(values, [1000.0, 3000.0, 7000.0])

    def test_clamped_escalation_and_finite_output(self):
        crazy_config = {
            "Steel": {"Nutzungsdauer": 50, "Preissteigerung": 100000},  # clamps to 100%
        }
        html = simulate_material_cost_projection_plotly(
            self.doc, crazy_config, years=100, material_id=self.mat1.id
        )
        self.assertIn("Steel", html)
        self.assertIn("plotly-graph", html)

    def test_plotly_labels_english(self):
        html = simulate_material_cost_projection_plotly(
            self.doc, self.config, material_id=self.mat1.id
        )
        self.assertIn("Time (Years)", html)
        self.assertIn("Cumulative Cost", html)
        self.assertIn("Rising Cost Over Time", html)

    def test_plot_material_costs_lists_all_materials_and_escapes_names(self):
        MaterialProperties.objects.create(
            project=self.doc,
            name="<script>alert(1)</script>",
            mass=1.0,
            local_brutto_price=42.0,
        )

        html = plot_material_costs(self.doc)

        self.assertIn("Steel", html)
        self.assertIn("Concrete", html)
        # The raw, unescaped payload must never appear verbatim. Plotly's own
        # JSON-in-<script> encoder further escapes the trailing "/" (e.g. as
        # "\u002f"), so we only assert on the html.escape()-d opening tag
        # rather than the exact combined escaping of the full string.
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;", html)

    def test_plot_material_costs_shows_total_local_gross_cost(self):
        # Only self.mat1 (100.0) and self.mat2 (500.0) exist at this point.
        html = plot_material_costs(self.doc)
        self.assertIn("600", html)
