"""Registry of all production and review agents."""
from .advanced import (AccessibilityReviewer, AudienceResearcher, ContentCalendarPlanner, LocalizationReviewer,
                       OriginalityReviewer, ProductionCostAnalyst, RightsReleaseAssistant, SourceCitation,
                       StoryStructureEditor, ThumbnailExperimentDesigner)
from .audio_agents import AudioMastering, CaptionTranslation, MusicSound, Narrator
from .director import Director
from .planning import (Analytics, BrandMemory, Compliance, FactChecker, HookRetention, QuotaManager, Research,
                       ScriptWriter, SecurityGuard, TrendAnalyst)
from .publishing import QA, SEO, EditorRenderer, LicenseAttribution, PublishingPackage, Thumbnail
from .repurpose import Repurposing
from .visuals import Picker, ShotPlanner, FootageFinder, VisualStyle


def build_registry():
    agents = [SecurityGuard(), QuotaManager(), BrandMemory(), Analytics(), TrendAnalyst(), Research(), ScriptWriter(),
              HookRetention(), FactChecker(), Compliance(), ShotPlanner(), VisualStyle(), FootageFinder(), Picker(),
              Narrator(), AudioMastering(), MusicSound(), CaptionTranslation(), EditorRenderer(), SEO(), Thumbnail(),
              LicenseAttribution(), PublishingPackage(), QA(), Repurposing(), AudienceResearcher(), SourceCitation(),
              OriginalityReviewer(), StoryStructureEditor(), AccessibilityReviewer(), LocalizationReviewer(),
              ThumbnailExperimentDesigner(), ContentCalendarPlanner(), RightsReleaseAssistant(), ProductionCostAnalyst()]
    registry = {a.id: a for a in agents}
    registry["director"] = Director(registry)
    return registry


def agent_catalog(registry):
    return sorted((a.meta() for a in registry.values()), key=lambda m: m["number"])
