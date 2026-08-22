-- =====================================================================
-- ARUNA AI - simpan Signal Quality bersama prediksinya (PASAL 11.1, 11.14)
--
-- Quality bukan confidence, dan itu sebabnya ia butuh kolom sendiri.
-- Confidence menjawab "seberapa yakin arahnya"; quality menjawab "seberapa
-- layak keseluruhan setup ini menghasilkan signal". Keduanya bisa berlawanan,
-- dan justru di situ gunanya: council yang sangat yakin di atas satu indikator
-- yang bertahan hidup dari data tipis, spread lebar, dan berita basi adalah
-- keyakinan yang tidak ditopang apa pun.
--
-- `quality_coverage` disimpan bersama skornya dan bukan sebagai catatan kaki.
-- 91/100 dari tujuh belas faktor dan 91/100 dari tiga faktor adalah dua
-- pernyataan yang sangat berbeda, dan tanpa cakupan keduanya tercetak identik.
-- Sebuah kolom skor tanpa kolom cakupan di sebelahnya akan membuat autopsi
-- setahun lagi membandingkan angka-angka yang tidak sebanding.
--
-- `quality_detail` menyimpan faktor per faktor - termasuk yang TIDAK terukur,
-- dengan alasannya. Itu bagian yang paling mudah hilang dan paling dibutuhkan
-- nanti: signal spot selalu kehilangan funding, open interest, dan likuidasi,
-- dan tanpa catatan itu skornya terlihat seperti hasil pengukuran lengkap.
--
-- Ketiganya NULL untuk baris lama. Itu benar dan disengaja: prediksi yang
-- dibuat sebelum skor ini ada tidak punya skor, dan mengarang satu untuknya
-- - misalnya dengan menghitung ulang dari data hari ini - persis yang PASAL
-- 11.21 larang.
-- =====================================================================

ALTER TABLE signal_snapshots
    ADD COLUMN signal_quality   TINYINT UNSIGNED NULL
        COMMENT '0-100, NULL untuk prediksi sebelum PASAL 11.1 ada',
    ADD COLUMN quality_coverage DECIMAL(5,4)     NULL
        COMMENT 'bagian bobot faktor yang benar-benar terukur, 0..1',
    ADD COLUMN quality_detail   JSON             NULL
        COMMENT 'faktor per faktor, termasuk yang tidak terukur beserta alasannya';

ALTER TABLE signal_snapshots
    ADD CONSTRAINT signal_snapshots_quality_range CHECK (
        signal_quality IS NULL OR (signal_quality >= 0 AND signal_quality <= 100)
    ),
    ADD CONSTRAINT signal_snapshots_coverage_range CHECK (
        quality_coverage IS NULL
        OR (quality_coverage >= 0 AND quality_coverage <= 1)
    ),
    -- Skor tanpa cakupan adalah angka yang tidak bisa dinilai pembacanya, dan
    -- cakupan tanpa skor adalah cakupan atas apa. Keduanya ada, atau tidak
    -- sama sekali.
    ADD CONSTRAINT signal_snapshots_quality_paired CHECK (
        (signal_quality IS NULL AND quality_coverage IS NULL)
        OR (signal_quality IS NOT NULL AND quality_coverage IS NOT NULL)
    );
