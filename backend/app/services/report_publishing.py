"""Shared online HTML publication for evaluation reports and task digests."""
import logging
from app.core.config import settings
from app.services import nami_deploy

log = logging.getLogger(__name__)


def publish_html(html, fallback_url=None, *, prefer_shortlink=None, existing_url=None):
    # 同一份日报的长链可重试换短链；已生成的短链始终复用，不重复上传。
    if existing_url and nami_deploy.is_short_url(existing_url):
        return {'url': existing_url, 'hosting': 'nami'}
    if settings.NAMI_DEPLOY_ENABLED and html:
        try:
            if nami_deploy.is_configured():
                if existing_url and prefer_shortlink:
                    url = nami_deploy.shorten_uploaded_url(existing_url)
                else:
                    url = (nami_deploy.deploy_html(html) if prefer_shortlink is None
                           else nami_deploy.deploy_html(html, prefer_shortlink=prefer_shortlink))
                if (settings.NAMI_SHORTLINK_ENABLED if prefer_shortlink is None else prefer_shortlink) and not nami_deploy.is_short_url(url):
                    log.warning('HTML publication returned the direct link; short-link service unavailable')
                return {'url': url, 'hosting': 'nami'}
        except Exception:
            log.warning('HTML publication unavailable; preserving the existing report link for retry')
    if existing_url:
        return {'url': existing_url, 'hosting': 'nami'}
    return {'url': fallback_url, 'hosting': 'platform'}
