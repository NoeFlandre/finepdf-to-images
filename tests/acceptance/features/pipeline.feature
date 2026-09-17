Feature: Bounded FinePDF pilot, end to end
  As a researcher running the proof of concept
  I want one pinned shard to become a set of agriculture-relevant documents and their images
  So that the result can be published and reproduced exactly

  Background:
    Given a pinned FinePDFs shard fixture
    And a fixture web server holding the source documents

  Scenario: An agriculture document becomes a published image artifact
    When I select 20 rows from the shard
    And I score the selected rows for agriculture relevance
    And I retrieve the documents for the relevant rows
    And I extract the images from the retrieved documents
    Then some rows are relevant and some are not
    And every relevant row has evidence naming the terms that matched
    And every retrieved document is stored under its own content hash
    And every extracted image links back to its page and its document
    And the publication policy refuses to republish any source bytes

  Scenario: The whole pipeline is reproducible
    When I run the whole pipeline twice
    Then both runs produce byte-identical manifests

  Scenario: A row whose text is not about agriculture is never fetched
    When I select 20 rows from the shard
    And I score the selected rows for agriculture relevance
    And I retrieve the documents for the relevant rows
    Then no request was made for an irrelevant row

  Scenario: An unsafe source URL is refused before any request
    Given a row whose url is "ftp://fixtures.invalid/doc.pdf"
    When I retrieve that row
    Then no request is made at all
    And the failure is recorded as "unsafe-url"

  Scenario: A server returning HTML publishes nothing
    Given a row whose document is served as HTML labelled application/pdf
    When I retrieve that row
    Then no artifact is stored
    And the failure is recorded as "not-pdf"

  Scenario: A malformed PDF fails without partial output
    Given a retrieved document whose bytes are not a readable PDF
    When I extract the images from the retrieved documents
    Then no image artifact is stored
    And the document is recorded as failed with a reason

  Scenario: A PDF with no images is a success
    Given a retrieved document that contains no images
    When I extract the images from the retrieved documents
    Then the document is recorded as a zero-image success

  Scenario: A document with empty text scores as not relevant
    Given a row whose extracted text is blank
    When I score that row
    Then it is not relevant and the result has no evidence

  Scenario: Publication is refused without a curated licence
    Given a retrieved document with no established licence
    When the publication policy evaluates it
    Then the decision is "metadata-only"
    And the provenance still traces back to the FinePDFs row
