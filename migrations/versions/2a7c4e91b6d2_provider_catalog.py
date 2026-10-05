from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '2a7c4e91b6d2'
down_revision = '1d9f2c7ab431'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'providers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(80), nullable=False),
        sa.Column('display_name', sa.String(160), nullable=False),
        sa.Column('category', sa.String(80), nullable=False, server_default='llm'),
        sa.Column('auth_type', sa.String(40), nullable=False),
        sa.Column('base_url', sa.String(2048)),
        sa.Column('docs_url', sa.String(2048)),
        sa.Column(
            'config',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('key', name='uq_providers_key'),
    )

    op.add_column('engines', sa.Column('provider_id', sa.Integer(), nullable=True))

    op.create_index(
        'ix_engines_provider_id',
        'engines',
        ['provider_id'],
        unique=False,
    )

    op.create_foreign_key(
        'fk_engines_provider_id',
        'engines',
        'providers',
        ['provider_id'],
        ['id'],
        ondelete='SET NULL',
    )

    providers = [
        ('openai', 'ChatGPT / OpenAI', 'llm', 'api_key', 'https://api.openai.com/v1', 'https://platform.openai.com/docs'),
        ('google_gemini', 'Google Gemini', 'llm', 'api_key', 'https://generativelanguage.googleapis.com', 'https://ai.google.dev/gemini-api/docs'),
        ('perplexity', 'Perplexity', 'search_llm', 'api_key', 'https://api.perplexity.ai', 'https://docs.perplexity.ai'),
        ('anthropic', 'Claude / Anthropic', 'llm', 'api_key', 'https://api.anthropic.com', 'https://docs.anthropic.com'),
        ('microsoft_copilot', 'Microsoft Copilot', 'copilot', 'entra_id', 'https://graph.microsoft.com', 'https://learn.microsoft.com/microsoft-365-copilot/'),
        ('xai', 'Grok / xAI', 'llm', 'api_key', 'https://api.x.ai', 'https://docs.x.ai'),
        ('deepseek', 'DeepSeek', 'llm', 'api_key', 'https://api.deepseek.com', 'https://api-docs.deepseek.com'),
        ('meta', 'Meta AI', 'llm', 'api_key', 'https://llama.meta.com', 'https://ai.meta.com/llama/'),
    ]

    for key, display_name, category, auth_type, base_url, docs_url in providers:
        op.execute(
            sa.text(
                "INSERT INTO providers "
                "(key, display_name, category, auth_type, base_url, docs_url, config, enabled, created_at, updated_at) "
                "VALUES (:key, :display_name, :category, :auth_type, :base_url, :docs_url, '{}'::jsonb, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ).bindparams(
                key=key,
                display_name=display_name,
                category=category,
                auth_type=auth_type,
                base_url=base_url,
                docs_url=docs_url,
            )
        )

    op.execute(
        sa.text(
            "UPDATE engines "
            "SET provider_id = (SELECT id FROM providers WHERE key = 'perplexity') "
            "WHERE key = 'perplexity'"
        )
    )


def downgrade():
    op.drop_constraint('fk_engines_provider_id', 'engines', type_='foreignkey')
    op.drop_index('ix_engines_provider_id', table_name='engines')
    op.drop_column('engines', 'provider_id')
    op.drop_table('providers')
