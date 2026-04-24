from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    query: Mapped[str] = mapped_column(String(512), nullable=False)
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="created", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    serp_results: Mapped[list["SerpResult"]] = relationship(back_populates="analysis_run")
    keywords: Mapped[list["AnalysisKeyword"]] = relationship(back_populates="analysis_run")
    page_snapshots: Mapped[list["PageSnapshot"]] = relationship(back_populates="analysis_run")
    page_features: Mapped[list["PageFeature"]] = relationship(back_populates="analysis_run")
    page_scores: Mapped[list["PageScore"]] = relationship(back_populates="analysis_run")
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="analysis_run")


class AnalysisKeyword(Base):
    __tablename__ = "analysis_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="keywords")
    serp_results: Mapped[list["SerpResult"]] = relationship(back_populates="keyword")


class SerpResult(Base):
    __tablename__ = "serp_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    keyword_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_keywords.id"), nullable=True, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    is_target: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="serp_results")
    keyword: Mapped[AnalysisKeyword | None] = relationship(back_populates="serp_results")


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "target" | "competitor"
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetch_status: Mapped[str] = mapped_column(String(64), default="pending", nullable=False)
    raw_html_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="page_snapshots")


class PageFeature(Base):
    __tablename__ = "page_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    meta_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    h1: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    h2_h3_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    main_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_faq: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_tables: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_lists: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_cta: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_forms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_prices: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_reviews: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_contacts: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    internal_links_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    external_links_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    has_schema_org: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="page_features")


class PageScore(Base):
    __tablename__ = "page_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    intent_fit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    structure_fit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    semantic_coverage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    commercial_fit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    trust_fit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    internal_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="page_scores")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    priority: Mapped[str] = mapped_column(String(32), nullable=False)  # urgent | medium | later
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    analysis_run: Mapped[AnalysisRun] = relationship(back_populates="recommendations")
