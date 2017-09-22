'''
Build the pipeline workflow by plumbing the stages together.
'''

from ruffus import Pipeline, suffix, formatter, add_inputs, output_from
from stages import Stages


def make_pipeline(state):
    '''Build the pipeline by constructing stages and connecting them together'''
    # Build an empty pipeline
    pipeline = Pipeline(name='hiplexpipe')
    # Get a list of paths to all the FASTQ files
    fastq_files = state.config.get_option('fastqs')
    # Stages are dependent on the state
    stages = Stages(state)

    # The original FASTQ files
    # This is a dummy stage. It is useful because it makes a node in the
    # pipeline graph, and gives the pipeline an obvious starting point.
    pipeline.originate(
        task_func=stages.original_fastqs,
        name='original_fastqs',
        output=fastq_files)

    # Align paired end reads in FASTQ to the reference producing a BAM file
    pipeline.transform(
        task_func=stages.align_bwa,
        name='align_bwa',
        input=output_from('original_fastqs'),
        # Match the R1 (read 1) FASTQ file and grab the path and sample name.
        # This will be the first input to the stage.
        # We assume the sample name may consist of only alphanumeric
        # characters.
        # Hi-Plex example: OHI031002-P02F04_S318_L001_R1_001.fastq
        # new sample name = OHI031002-P02F04
        # 100ng-GSP4-cy0-251_S251_L001_R2_001.fastq.gz
        # HardSeed-4C-096_S96_L001_R1_001.fastq.gz
        filter=formatter(
            '.+/(?P<sample>[a-zA-Z0-9-]+)_(?P<readid>[a-zA-Z0-9-]+)_(?P<lane>[a-zA-Z0-9]+)_R1_(?P<lib>[a-zA-Z0-9-:]+).fastq'),
        # Add one more inputs to the stage:
        #    1. The corresponding R2 FASTQ file
        # Hi-Plex example: OHI031002-P02F04_S318_L001_R2_001.fastq
        add_inputs=add_inputs(
            '{path[0]}/{sample[0]}_{readid[0]}_{lane[0]}_R2_{lib[0]}.fastq'),
        # Add an "extra" argument to the state (beyond the inputs and outputs)
        # which is the sample name. This is needed within the stage for finding out
        # sample specific configuration options
        extras=['{sample[0]}', '{readid[0]}', '{lane[0]}', '{lib[0]}'],
        # The output file name is the sample name with a .bam extension.
        output='alignments/{sample[0]}_{readid[0]}/{sample[0]}_{readid[0]}.bam')

    # Call variants using undr_rover
    pipeline.transform(
        task_func=stages.apply_undr_rover,
        name='apply_undr_rover',
        input=output_from('original_fastqs'),
        # Match the R1 (read 1) FASTQ file and grab the path and sample name.
        # This will be the first input to the stage.
        # We assume the sample name may consist of only alphanumeric
        # characters.
        # Hi-Plex example: OHI031002-P02F04_S318_L001_R1_001.fastq
        filter=formatter(
            '.+/(?P<sample>[a-zA-Z0-9-]+)_(?P<readid>[a-zA-Z0-9-]+)_(?P<lane>[a-zA-Z0-9]+)_R1_(?P<lib>[a-zA-Z0-9-:]+).fastq'),
        # Add one more inputs to the stage:
        #    1. The corresponding R2 FASTQ file
        # Hi-Plex example: OHI031002-P02F04_S318_L001_R2_001.fastq

        add_inputs=add_inputs(
            '{path[0]}/{sample[0]}_{readid[0]}_{lane[0]}_R2_{lib[0]}.fastq'),
        # extras=['{sample[0]}', '{readid[0]}', '{lane[0]}', '{lib[0]}'],
        extras=['{sample[0]}', '{readid[0]}'],
        # The output file name is the sample name with a .bam extension.
        output='variants/undr_rover/{sample[0]}_{readid[0]}.vcf')

    # Sort the BAM file using Picard
    pipeline.transform(
        task_func=stages.sort_bam_picard,
        name='sort_bam_picard',
        input=output_from('align_bwa'),
        filter=suffix('.bam'),
        output='.sort.bam')

    # index bam file
    pipeline.transform(
        task_func=stages.index_sort_bam_picard,
        name='index_bam',
        input=output_from('sort_bam_picard'),
        filter=suffix('.sort.bam'),
        output='.sort.bam.bai')

    # Clip the primer_seq from BAM File
    (pipeline.transform(
        task_func=stages.clip_bam,
        name='clip_bam',
        input=output_from('sort_bam_picard'),
        filter=suffix('.bam'),
        output='.clip.bam')
        .follows('index_bam'))

    # # Sort the BAM file using Picard
    # pipeline.transform(
    #     task_func=stages.sort_bam_picard,
    #     name='sort_bam_picard',
    #     input=output_from('clip_bam'),
    #     filter=suffix('.clip.bam'),
    #     output='.sort.bam')

    # samtools index sorted bam file
    pipeline.transform(
        task_func=stages.index_sort_bam_picard,
        name='index_sort_bam_picard',
        input=output_from('clip_bam'),
        filter=suffix('.clip.bam'),
        output='.clip.bam.bai')

    # # Coverage using Picard
    # (pipeline.transform(
    #     task_func=stages.target_coverage,
    #     name='target_coverage',
    #     input=output_from('sort_bam_picard'),
    #     # filter=suffix('.sort.bam'),
    #     filter=formatter(
    #         '.+/(?P<sample>[a-zA-Z0-9-_]+).sort.bam'),
    #     output='coverage/{sample[0]}.coverage.txt')
    #     .follows('sort_bam_picard'))

    ###### GATK VARIANT CALLING ######
    # Call variants using GATK
    (pipeline.transform(
        task_func=stages.call_haplotypecaller_gatk,
        name='call_haplotypecaller_gatk',
        input=output_from('sort_bam_picard'),
        # filter=suffix('.merged.dedup.realn.bam'),
        filter=formatter('.+/(?P<sample>[a-zA-Z0-9-]+).sort.bam'),
        output='variants/gatk/{sample[0]}.g.vcf')
        .follows('index_sort_bam_picard'))

    # Combine G.VCF files for all samples using GATK
    pipeline.merge(
        task_func=stages.combine_gvcf_gatk,
        name='combine_gvcf_gatk',
        input=output_from('call_haplotypecaller_gatk'),
        output='variants/gatk/ALL.combined.vcf')

    # Genotype G.VCF files using GATK
    pipeline.transform(
        task_func=stages.genotype_gvcf_gatk,
        name='genotype_gvcf_gatk',
        input=output_from('combine_gvcf_gatk'),
        filter=suffix('.combined.vcf'),
        output='.raw.vcf')

    # Annotate VCF file using GATK
    pipeline.transform(
       task_func=stages.variant_annotator_gatk,
       name='variant_annotator_gatk',
       input=output_from('genotype_gvcf_gatk'),
       filter=suffix('.raw.vcf'),
       output='.raw.annotate.vcf')
    ###### GATK VARIANT CALLING ######


    # # Apply samtools
    # pipeline.merge(
    #     task_func=stages.apply_samtools_mpileup,
    #     name='apply_samtools_mpileup',
    #     input=output_from('sort_bam_picard'),
    #     # filter=suffix('.sort.bam'),
    #     #filter=formatter('.+/(?P<sample>[a-zA-Z0-9-]+).sort.bam'),
    #     output='variants/all.mpileup')
    #     #filter=formatter('.+/(?P<sample>[a-zA-Z0-9-]+).sort.bam'),
    #     #output='variants/all.bcf')
    #     #.follows('sort_bam_picard'))
    #
    # # Apply bcftools
    # (pipeline.transform(
    #     task_func=stages.apply_bcftools,
    #     name='apply_bcftools',
    #     input=output_from('apply_samtools_mpileup'),
    #     filter=suffix('.mpileup'),
    #     # add_inputs=add_inputs(['variants/ALL.indel_recal', 'variants/ALL.indel_tranches']),
    #     output='.raw.vcf')
    #     .follows('apply_samtools_mpileup'))
    #
    # # Apply NORM
    # (pipeline.transform(
    #     task_func=stages.apply_vt,
    #     name='apply_vt',
    #     input=output_from('apply_bcftools'),
    #     filter=suffix('.raw.vcf'),
    #     # add_inputs=add_inputs(['variants/ALL.indel_recal', 'variants/ALL.indel_tranches']),
    #     output='.raw.vt.vcf')
    #     .follows('sort_bam_picard'))
    #
    # # Apply VEP
    # (pipeline.transform(
    #     task_func=stages.apply_vep,
    #     name='apply_vep',
    #     input=output_from('apply_vt'),
    #     filter=suffix('.raw.vt.vcf'),
    #     # add_inputs=add_inputs(['variants/ALL.indel_recal', 'variants/ALL.indel_tranches']),
    #     output='.raw.vt.vep.vcf')
    #     .follows('apply_vt'))

    # # Apply SnpEff
    # (pipeline.transform(
    #     task_func=stages.apply_snpeff,
    #     name='apply_snpeff',
    #     input=output_from('apply_vep'),
    #     filter=suffix('.raw.vt.vep.vcf'),
    #     # add_inputs=add_inputs(['variants/ALL.indel_recal', 'variants/ALL.indel_tranches']),
    #     output='.raw.vt.vep.snpeff.vcf')
    #     .follows('apply_vep'))
    #
    # # Apply vcfanno
    # (pipeline.transform(
    #     task_func=stages.apply_vcfanno,
    #     name='apply_vcfanno',
    #     input=output_from('apply_snpeff'),
    #     filter=suffix('.raw.vt.vep.snpeff.vcf'),
    #     # add_inputs=add_inputs(['variants/ALL.indel_recal', 'variants/ALL.indel_tranches']),
    #     output='.annotated.vcf')
    #     .follows('apply_snpeff'))

    # Concatenate undr_rover vcf files
    pipeline.merge(
        task_func=stages.apply_cat_vcf,
        name='apply_cat_vcf',
        input=output_from('apply_undr_rover'),
        output='variants/undr_rover/ur.vcf.gz')

    # Apple VEP on concatenated undr_rover vcf file
    (pipeline.transform(
        task_func=stages.apply_vep,
        name='apply_vep_ur',
        input=output_from('apply_cat_vcf'),
        filter=suffix('.vcf.gz'),
        output='.vep.vcf')
        .follows('apply_cat_vcf'))

    # Apply vcfanno on concatenated/vep undr_rover vcf file
    (pipeline.transform(
        task_func=stages.apply_vcfanno,
        name='apply_vcfanno_ur',
        input=output_from('apply_vep_ur'),
        filter=suffix('.vep.vcf'),
        output='.vep.anno.vcf')
        .follows('apply_vep_ur'))

    # Apply snpeff
    (pipeline.transform(
        task_func=stages.apply_snpeff,
        name='apply_snpeff_ur',
        input=output_from('apply_vcfanno_ur'),
        filter=suffix('.vep.anno.vcf'),
        output='.vep.anno.snpeff.vcf')
        .follows('apply_vcfanno_ur'))

    # Apply summarize multi coverage
    (pipeline.merge(
        task_func=stages.apply_multicov,
        name='apply_multicov',
        input=output_from('sort_bam_picard'),
        output='coverage/all.multicov.txt')
        .follows('index_sort_bam_picard'))

    # Apply summarize picard coverage
    # (pipeline.merge(
    #     task_func=stages.apply_summarize_picard,
    #     name='apply_summarize_picard',
    #     input=output_from('target_coverage'),
    #     output='coverage/all.hsmetrics.txt')
    #     .follows('target_coverage'))

    # Apply summarize multicov coverage plots
    (pipeline.merge(
        task_func=stages.apply_multicov_plots,
        name='apply_multicov_plots',
        input=output_from('apply_multicov'),
        output='coverage/coverage_analysis_main.html')
        .follows('apply_multicov'))

    return pipeline
