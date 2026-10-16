"""Shared online HTML publication for evaluation reports and task digests."""
import logging
from app.core.config import settings
from app.services import nami_deploy

log = logging.getLogger(__name__)


def publish_html(html, fallback_url=None, *, prefer_shortlink=None):
    if settings.NAMI_DEPLOY_ENABLED and html:
        try:
            if nami_deploy.is_configured():
                url = (nami_deploy.deploy_html(html) if prefer_shortlink is None
                       else nami_deploy.deploy_html(html, prefer_shortlink=prefer_shortlink))
                if (settings.NAMI_SHORTLINK_ENABLED if prefer_shortlink is None else prefer_shortlink) and '/client-up/' in url:
                    log.warning('HTML publication returned the direct link; short-link service unavailable')
                return {'url': url, 'hosting': 'nami'}
        except Exception:
            log.warning('HTML publication unavailable; using the existing platform report link')
    return {'url': fallback_url, 'hosting': 'platform'}
