"""Terms of service text and its version, in one place.

`VERSION` is the single source of truth. `users.terms_version` records it at
signup, the /terms page displays it, and a test asserts the three agree - so a
revision to the text cannot silently leave accounts recorded against the wrong
version, which is exactly the detail that matters if acceptance is ever disputed.

Revising the terms means: edit SECTIONS, bump VERSION and EFFECTIVE_DATE. Existing
accounts keep the version they actually accepted; they are not retroactively moved.
"""

VERSION = '2026-10-01'
EFFECTIVE_DATE = '1 October 2026'

SERVICE_NAME = 'trySearch'
CONTACT_EMAIL = 'support@trysearch.aevix.xyz'

# Plain-language operative terms for an AI-search visibility SaaS. Written to be
# accurate about what the product actually does rather than to be exhaustive.
SECTIONS = (
    (
        'Who these terms are between',
        [
            f'These terms govern your use of {SERVICE_NAME}, an AI-search visibility '
            'and answer-engine-optimisation platform. "We" and "us" mean the operator '
            f'of {SERVICE_NAME}; "you" means the person or organisation using it.',
            'By creating an account you confirm you have read these terms and agree '
            'to them, and that you are authorised to accept them on behalf of any '
            'organisation you register.',
        ],
    ),
    (
        'Your account',
        [
            'You must give an email address you control and confirm it before using '
            'the product. Keep your password secret; you are responsible for activity '
            'carried out through your account.',
            'One person may not share a single account with others in place of adding '
            'them as members of an organisation.',
            'We may suspend an account that is being used to break these terms, to '
            'attack the service, or in a way that puts other customers at risk.',
        ],
    ),
    (
        'What the service does, and what it cannot promise',
        [
            f'{SERVICE_NAME} sends prompts you choose to third-party AI engines, '
            'records their answers, and measures whether your brand is mentioned or '
            'cited. Those answers come from systems we do not control.',
            'AI engines are non-deterministic and change without notice. Identical '
            'prompts can produce different answers at different times. Measurements '
            'describe what an engine said when it was asked, not a guaranteed or '
            'repeatable ranking.',
            'We do not promise that using the product will improve your visibility in '
            'any AI engine, search engine or other system. Recommendations are '
            'suggestions based on observed data, not assurances of a result.',
        ],
    ),
    (
        'Acceptable use',
        [
            'Do not use the service to break the law, to infringe anyone\'s rights, or '
            'to breach the terms of the AI engines it queries.',
            'Do not attempt to gain access to data belonging to other customers, to '
            'probe or load-test the service without written permission, or to resell '
            'access unless your plan expressly allows it.',
            'Do not use the product to generate content you then present as '
            'independent or impartial when it is not.',
        ],
    ),
    (
        'Your data',
        [
            'You keep ownership of the websites, brands, prompts and content you add. '
            'You grant us only the permission needed to operate the service for you: '
            'to fetch your public pages, send your prompts to the engines you enable, '
            'and store the results.',
            'We store the raw answers engines return, so that the numbers we show can '
            'be traced back to the evidence they came from.',
            'We do not sell your data. We use third-party AI and infrastructure '
            'providers to deliver the service, and your prompts are sent to the '
            'engines you choose to enable.',
        ],
    ),
    (
        'Plans, usage and payment',
        [
            'Plan limits — such as workspaces, tracked prompts, scan frequency and '
            'engine access — are enforced by the service and shown in your account.',
            'Where a plan is paid, fees are charged in advance for the billing period '
            'and are not refundable for a period already started, except where the law '
            'requires otherwise.',
            'Running scans consumes third-party capacity that we pay for. We may apply '
            'reasonable usage ceilings to protect the service, and will tell you when '
            'you are approaching one.',
        ],
    ),
    (
        'Availability and changes',
        [
            'We aim to keep the service available but do not guarantee uninterrupted '
            'operation. Maintenance, provider outages and engine changes can interrupt '
            'scanning.',
            'We may change features as the product develops. We will not remove a '
            'capability your paid plan depends on without reasonable notice.',
        ],
    ),
    (
        'Ending your use',
        [
            'You may stop using the service and close your account at any time.',
            'If you close your account we will delete or anonymise your data within a '
            'reasonable period, except where we must keep records to meet a legal '
            'obligation.',
        ],
    ),
    (
        'Liability',
        [
            'The service is provided as-is to the extent the law allows. We are not '
            'liable for lost profits, lost revenue, or decisions you take based on the '
            'measurements and recommendations the product produces.',
            'Nothing in these terms limits liability that cannot lawfully be limited.',
        ],
    ),
    (
        'Changes to these terms',
        [
            'We may update these terms. The version in force is shown at the top of '
            'this page, and the version you accepted is recorded against your account '
            'when you sign up.',
            'If a change materially reduces your rights we will ask you to accept the '
            'new version before you continue using the service.',
        ],
    ),
    (
        'Contact',
        [
            f'Questions about these terms: {CONTACT_EMAIL}.',
        ],
    ),
)


def summary():
    """Metadata for an API or template, without the full text."""
    return {
        'version': VERSION,
        'effective_date': EFFECTIVE_DATE,
        'url': '/terms',
        'section_count': len(SECTIONS),
    }
