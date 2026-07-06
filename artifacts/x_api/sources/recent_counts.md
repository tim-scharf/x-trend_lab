> ## Documentation Index
> Fetch the complete documentation index at: https://docs.x.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Get count of recent Posts

> Retrieves the count of Posts from the last 7 days matching a search query.



## OpenAPI

````yaml get /2/tweets/counts/recent
openapi: 3.0.0
info:
  description: X API v2 available endpoints
  version: '2.166'
  title: X API v2
  termsOfService: https://developer.x.com/en/developer-terms/agreement-and-policy.html
  contact:
    name: X Developers
    url: https://developer.x.com/
  license:
    name: X Developer Agreement and Policy
    url: https://developer.x.com/en/developer-terms/agreement-and-policy.html
servers:
  - description: X API
    url: https://api.x.com
security: []
tags:
  - name: Account Activity
    description: Endpoints relating to retrieving, managing AAA subscriptions
    externalDocs:
      description: Find out more
      url: >-
        https://docs.x.com/x-api/enterprise-gnip-2.0/fundamentals/account-activity
  - name: Articles
    description: Endpoints related to retrieving, creating & modifying Articles
    externalDocs:
      description: Find out more
      url: https://developer.x.com/en/docs/twitter-api/tweets/lookup
  - name: Bookmarks
    description: Endpoints related to retrieving, managing bookmarks of a user
    externalDocs:
      description: Find out more
      url: https://developer.twitter.com/en/docs/twitter-api/bookmarks
  - name: Compliance
    description: Endpoints related to keeping X data in your systems compliant
    externalDocs:
      description: Find out more
      url: >-
        https://developer.twitter.com/en/docs/twitter-api/compliance/batch-tweet/introduction
  - name: Connections
    description: Endpoints related to streaming connections
    externalDocs:
      description: Find out more
      url: https://developer.x.com/en/docs/x-api/connections
  - name: Direct Messages
    description: Endpoints related to retrieving, managing Direct Messages
    externalDocs:
      description: Find out more
      url: https://developer.twitter.com/en/docs/twitter-api/direct-messages
  - name: General
    description: Miscellaneous endpoints for general API functionality
    externalDocs:
      description: Find out more
      url: https://developer.twitter.com/en/docs/twitter-api
  - name: Lists
    description: Endpoints related to retrieving, managing Lists
    externalDocs:
      description: Find out more
      url: https://developer.twitter.com/en/docs/twitter-api/lists
  - name: Marketplace
    description: Endpoints related to marketplace handles
    externalDocs:
      description: Handle marketplace availability
      url: https://docs.x.com/x-api/marketplace/handles/availability
  - name: Media
    description: Endpoints related to Media
    externalDocs:
      description: Find out more
      url: https://developer.x.com
  - name: MediaUpload
    description: Endpoints related to uploading Media
    externalDocs:
      description: Find out more
      url: https://developer.x.com
  - name: News
    description: Endpoint for retrieving news stories
    externalDocs:
      description: Find out more
      url: https://developer.twitter.com/en/docs/twitter-api/news
  - name: Spaces
    description: Endpoints related to retrieving, managing Spaces
    externalDocs:
      description: Find out more
      url: https://developer.twitter.com/en/docs/twitter-api/spaces
  - name: Stream
    description: Endpoints related to streaming
    externalDocs:
      description: Find out more
      url: https://developer.x.com
  - name: Tweets
    description: Endpoints related to retrieving, searching, and modifying Tweets
    externalDocs:
      description: Find out more
      url: https://developer.twitter.com/en/docs/twitter-api/tweets/lookup
  - name: Users
    description: Endpoints related to retrieving, managing relationships of Users
    externalDocs:
      description: Find out more
      url: https://developer.twitter.com/en/docs/twitter-api/users/lookup
paths:
  /2/tweets/counts/recent:
    get:
      tags:
        - Tweets
      summary: Get count of recent Posts
      description: >-
        Retrieves the count of Posts from the last 7 days matching a search
        query.
      operationId: getPostsCountsRecent
      parameters:
        - name: query
          in: query
          description: >-
            One query/rule/filter for matching Posts. Refer to
            https://t.co/rulelength to identify the max query length.
          required: true
          schema:
            type: string
            minLength: 1
            maxLength: 4096
            example: (from:TwitterDev OR from:TwitterAPI) has:media -is:retweet
          style: form
        - name: start_time
          in: query
          description: >-
            YYYY-MM-DDTHH:mm:ssZ. The oldest UTC timestamp (from most recent 7
            days) from which the Posts will be provided. Timestamp is in second
            granularity and is inclusive (i.e. 12:00:01 includes the first
            second of the minute).
          required: false
          schema:
            type: string
            format: date-time
          style: form
        - name: end_time
          in: query
          description: >-
            YYYY-MM-DDTHH:mm:ssZ. The newest, most recent UTC timestamp to which
            the Posts will be provided. Timestamp is in second granularity and
            is exclusive (i.e. 12:00:01 excludes the first second of the
            minute).
          required: false
          schema:
            type: string
            format: date-time
          style: form
        - name: since_id
          in: query
          description: >-
            Returns results with a Post ID greater than (that is, more recent
            than) the specified ID.
          required: false
          schema:
            $ref: '#/components/schemas/TweetId'
          style: form
        - name: until_id
          in: query
          description: >-
            Returns results with a Post ID less than (that is, older than) the
            specified ID.
          required: false
          schema:
            $ref: '#/components/schemas/TweetId'
          style: form
        - name: next_token
          in: query
          description: >-
            This parameter is used to get the next 'page' of results. The value
            used with the parameter is pulled directly from the response
            provided by the API, and should not be modified.
          required: false
          schema:
            $ref: '#/components/schemas/PaginationToken36'
          style: form
        - name: pagination_token
          in: query
          description: >-
            This parameter is used to get the next 'page' of results. The value
            used with the parameter is pulled directly from the response
            provided by the API, and should not be modified.
          required: false
          schema:
            $ref: '#/components/schemas/PaginationToken36'
          style: form
        - name: granularity
          in: query
          description: The granularity for the search counts results.
          required: false
          schema:
            type: string
            enum:
              - minute
              - hour
              - day
            default: hour
          style: form
        - $ref: '#/components/parameters/SearchCountFieldsParameter'
      responses:
        '200':
          description: The request has succeeded.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Get2TweetsCountsRecentResponse'
        default:
          description: The request has failed.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
            application/problem+json:
              schema:
                $ref: '#/components/schemas/Problem'
      security:
        - BearerToken: []
      externalDocs:
        url: >-
          https://developer.twitter.com/en/docs/twitter-api/tweets/counts/api-reference/get-tweets-counts-recent
components:
  schemas:
    TweetId:
      type: string
      description: >-
        Unique identifier of this Tweet. This is returned as a string in order
        to avoid complications with languages and tools that cannot handle large
        integers.
      pattern: ^[0-9]{1,19}$
      example: '1346889436626259968'
    PaginationToken36:
      type: string
      description: A base36 pagination token.
      minLength: 1
    Get2TweetsCountsRecentResponse:
      type: object
      properties:
        data:
          type: array
          minItems: 1
          items:
            $ref: '#/components/schemas/SearchCount'
        errors:
          type: array
          minItems: 1
          items:
            $ref: '#/components/schemas/Problem'
        meta:
          type: object
          properties:
            newest_id:
              $ref: '#/components/schemas/NewestId'
            next_token:
              $ref: '#/components/schemas/NextToken'
            oldest_id:
              $ref: '#/components/schemas/OldestId'
            total_tweet_count:
              $ref: '#/components/schemas/Aggregate'
    Error:
      type: object
      required:
        - code
        - message
      properties:
        code:
          type: integer
          format: int32
        message:
          type: string
    Problem:
      type: object
      description: >-
        An HTTP Problem Details object, as defined in IETF RFC 7807
        (https://tools.ietf.org/html/rfc7807).
      required:
        - type
        - title
      properties:
        detail:
          type: string
        status:
          type: integer
        title:
          type: string
        type:
          type: string
      discriminator:
        propertyName: type
        mapping:
          about:blank:
            $ref: '#/components/schemas/GenericProblem'
          https://api.twitter.com/2/problems/client-disconnected:
            $ref: '#/components/schemas/ClientDisconnectedProblem'
          https://api.twitter.com/2/problems/client-forbidden:
            $ref: '#/components/schemas/ClientForbiddenProblem'
          https://api.twitter.com/2/problems/conflict:
            $ref: '#/components/schemas/ConflictProblem'
          https://api.twitter.com/2/problems/disallowed-resource:
            $ref: '#/components/schemas/DisallowedResourceProblem'
          https://api.twitter.com/2/problems/duplicate-rules:
            $ref: '#/components/schemas/DuplicateRuleProblem'
          https://api.twitter.com/2/problems/invalid-request:
            $ref: '#/components/schemas/InvalidRequestProblem'
          https://api.twitter.com/2/problems/invalid-rules:
            $ref: '#/components/schemas/InvalidRuleProblem'
          https://api.twitter.com/2/problems/noncompliant-rules:
            $ref: '#/components/schemas/NonCompliantRulesProblem'
          https://api.twitter.com/2/problems/not-authorized-for-field:
            $ref: '#/components/schemas/FieldUnauthorizedProblem'
          https://api.twitter.com/2/problems/not-authorized-for-resource:
            $ref: '#/components/schemas/ResourceUnauthorizedProblem'
          https://api.twitter.com/2/problems/operational-disconnect:
            $ref: '#/components/schemas/OperationalDisconnectProblem'
          https://api.twitter.com/2/problems/resource-not-found:
            $ref: '#/components/schemas/ResourceNotFoundProblem'
          https://api.twitter.com/2/problems/resource-unavailable:
            $ref: '#/components/schemas/ResourceUnavailableProblem'
          https://api.twitter.com/2/problems/rule-cap:
            $ref: '#/components/schemas/RulesCapProblem'
          https://api.twitter.com/2/problems/streaming-connection:
            $ref: '#/components/schemas/ConnectionExceptionProblem'
          https://api.twitter.com/2/problems/unsupported-authentication:
            $ref: '#/components/schemas/UnsupportedAuthenticationProblem'
          https://api.twitter.com/2/problems/usage-capped:
            $ref: '#/components/schemas/UsageCapExceededProblem'
    SearchCount:
      type: object
      description: Represent a Search Count Result.
      required:
        - end
        - start
        - tweet_count
      properties:
        end:
          $ref: '#/components/schemas/End'
        start:
          $ref: '#/components/schemas/Start'
        tweet_count:
          $ref: '#/components/schemas/TweetCount'
    NewestId:
      type: string
      description: The newest id in this response.
    NextToken:
      type: string
      description: The next token.
      minLength: 1
    OldestId:
      type: string
      description: The oldest id in this response.
    Aggregate:
      type: integer
      description: The sum of results returned in this response.
      format: int32
    GenericProblem:
      description: >-
        A generic problem with no additional information beyond that provided by
        the HTTP status code.
      allOf:
        - $ref: '#/components/schemas/Problem'
    ClientDisconnectedProblem:
      description: Your client has gone away.
      allOf:
        - $ref: '#/components/schemas/Problem'
    ClientForbiddenProblem:
      description: >-
        A problem that indicates your client is forbidden from making this
        request.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          properties:
            reason:
              type: string
              enum:
                - official-client-forbidden
                - client-not-enrolled
            registration_url:
              type: string
              format: uri
    ConflictProblem:
      description: You cannot create a new job if one is already in progress.
      allOf:
        - $ref: '#/components/schemas/Problem'
    DisallowedResourceProblem:
      description: >-
        A problem that indicates that the resource requested violates the
        precepts of this API.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          required:
            - resource_id
            - resource_type
            - section
          properties:
            resource_id:
              type: string
            resource_type:
              type: string
              enum:
                - user
                - tweet
                - media
                - list
                - space
            section:
              type: string
              enum:
                - data
                - includes
    DuplicateRuleProblem:
      description: The rule you have submitted is a duplicate.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          properties:
            id:
              type: string
            value:
              type: string
    InvalidRequestProblem:
      description: A problem that indicates this request is invalid.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          properties:
            errors:
              type: array
              minItems: 1
              items:
                type: object
                properties:
                  message:
                    type: string
                  parameters:
                    type: object
                    additionalProperties:
                      type: array
                      items:
                        type: string
    InvalidRuleProblem:
      description: The rule you have submitted is invalid.
      allOf:
        - $ref: '#/components/schemas/Problem'
    NonCompliantRulesProblem:
      description: A problem that indicates the user's rule set is not compliant.
      allOf:
        - $ref: '#/components/schemas/Problem'
    FieldUnauthorizedProblem:
      description: >-
        A problem that indicates that you are not allowed to see a particular
        field on a Tweet, User, etc.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          required:
            - resource_type
            - field
            - section
          properties:
            field:
              type: string
            resource_type:
              type: string
              enum:
                - user
                - tweet
                - media
                - list
                - space
            section:
              type: string
              enum:
                - data
                - includes
    ResourceUnauthorizedProblem:
      description: >-
        A problem that indicates you are not allowed to see a particular Tweet,
        User, etc.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          required:
            - value
            - resource_id
            - resource_type
            - section
            - parameter
          properties:
            parameter:
              type: string
            resource_id:
              type: string
            resource_type:
              type: string
              enum:
                - user
                - tweet
                - media
                - list
                - space
            section:
              type: string
              enum:
                - data
                - includes
            value:
              type: string
    OperationalDisconnectProblem:
      description: You have been disconnected for operational reasons.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          properties:
            disconnect_type:
              type: string
              enum:
                - OperationalDisconnect
                - UpstreamOperationalDisconnect
                - ForceDisconnect
                - UpstreamUncleanDisconnect
                - SlowReader
                - InternalError
                - ClientApplicationStateDegraded
                - InvalidRules
    ResourceNotFoundProblem:
      description: A problem that indicates that a given Tweet, User, etc. does not exist.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          required:
            - parameter
            - value
            - resource_id
            - resource_type
          properties:
            parameter:
              type: string
              minLength: 1
            resource_id:
              type: string
            resource_type:
              type: string
              enum:
                - user
                - tweet
                - media
                - list
                - space
                - place
                - poll
            value:
              type: string
              description: Value will match the schema of the field.
    ResourceUnavailableProblem:
      description: >-
        A problem that indicates a particular Tweet, User, etc. is not available
        to you.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          required:
            - parameter
            - resource_id
            - resource_type
          properties:
            parameter:
              type: string
              minLength: 1
            resource_id:
              type: string
            resource_type:
              type: string
              enum:
                - user
                - tweet
                - media
                - list
                - space
    RulesCapProblem:
      description: You have exceeded the maximum number of rules.
      allOf:
        - $ref: '#/components/schemas/Problem'
    ConnectionExceptionProblem:
      description: A problem that indicates something is wrong with the connection.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          properties:
            connection_issue:
              type: string
              enum:
                - TooManyConnections
                - ProvisioningSubscription
                - RuleConfigurationIssue
                - RulesInvalidIssue
    UnsupportedAuthenticationProblem:
      description: A problem that indicates that the authentication used is not supported.
      allOf:
        - $ref: '#/components/schemas/Problem'
    UsageCapExceededProblem:
      description: A problem that indicates that a usage cap has been exceeded.
      allOf:
        - $ref: '#/components/schemas/Problem'
        - type: object
          properties:
            period:
              type: string
              enum:
                - Daily
                - Monthly
            scope:
              type: string
              enum:
                - Account
                - Product
    End:
      type: string
      description: The end time of the bucket.
      format: date-time
    Start:
      type: string
      description: The start time of the bucket.
      format: date-time
    TweetCount:
      type: integer
      description: The count for the bucket.
  parameters:
    SearchCountFieldsParameter:
      name: search_count.fields
      in: query
      description: A comma separated list of SearchCount fields to display.
      required: false
      schema:
        type: array
        description: The fields available for a SearchCount object.
        minItems: 1
        uniqueItems: true
        items:
          type: string
          enum:
            - end
            - start
            - tweet_count
        example:
          - end
          - start
          - tweet_count
      explode: false
      style: form
  securitySchemes:
    BearerToken:
      type: http
      scheme: bearer

````