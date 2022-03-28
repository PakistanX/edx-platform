(function(define) {
    'use strict';

    define(
        [
            'underscore',
            'backbone',
            'common/js/discussion/utils',
            'common/js/discussion/views/discussion_thread_view'
        ],
        function(_, Backbone, DiscussionUtil, DiscussionThreadView) {
            var DiscussionRouter = Backbone.Router.extend({
                routes: {
                    '': 'allThreads',
                    ':forum_name/threads/:thread_id': 'showThread'
                },

                initialize: function(options) {
                    Backbone.Router.prototype.initialize.call(this);
                    _.bindAll(this, 'allThreads', 'showThread');
                    this.rootUrl = options.rootUrl;
                    this.discussion = options.discussion;
                    this.courseSettings = options.courseSettings;
                    this.discussionBoardView = options.discussionBoardView;
                    this.newPostView = options.newPostView;
                    if (options.startHeader !== undefined) {
                        this.startHeader = options.startHeader;
                    } else {
                        this.startHeader = 2; // Start the header levels at H<startHeader>
                    }
                },

                start: function() {
                    var self = this,
                        $newPostButton = $('.new-post-btn');
                    this.listenTo(this.newPostView, 'newPost:cancel', this.hideNewPost);
                    $newPostButton.bind('click', _.bind(this.showNewPost, this));
                    $newPostButton.bind('keydown', function(event) {
                        DiscussionUtil.activateOnSpace(event, self.showNewPost);
                    });

                    // Automatically navigate when the user selects threads
                    this.discussionBoardView.discussionThreadListView.on(
                        'thread:selected', _.bind(this.navigateToThread, this)
                    );
                    this.discussionBoardView.discussionThreadListView.on(
                        'thread:removed', _.bind(this.navigateToAllThreads, this)
                    );
                    this.discussionBoardView.discussionThreadListView.on(
                        'threads:rendered', _.bind(this.setActiveThread, this)
                    );
                    this.discussionBoardView.discussionThreadListView.on(
                        'thread:created', _.bind(this.navigateToThread, this)
                    );

                    Backbone.history.start({
                        pushState: true,
                        root: this.rootUrl
                    });
                },

                stop: function() {
                    Backbone.history.stop();
                },

                allThreads: function() {
                    if(!this.discussionBoardView.discussionThreadListView.checkAndActivateThread()){
                        return this.discussionBoardView.goHome();
                    }
                },

                setActiveThread: function() {
                    if (this.thread) {
                        return this.discussionBoardView.discussionThreadListView.setActiveThread(this.thread.get('id'));
                    } else {
                        return this.discussionBoardView.goHome;
                    }
                },

                showThread: function(forumName, threadId) {
                    this.thread = this.discussion.get(threadId);
                    this.thread.set('unread_comments_count', 0);
                    this.thread.set('read', true);
                    this.setActiveThread();
                    return this.showMain();
                },

                showMain: function() {
                    var self = this;
                    if (this.main) {
                        this.main.cleanup();
                        this.main.undelegateEvents();
                    }
                    if (!(DiscussionUtil.forumDiv.is(':visible'))) {
                        DiscussionUtil.forumDiv.fadeIn();
                    }
                    if ($('.new-post-article').is(':visible')) {
                        $('.new-post-article').fadeOut();
                    }
                    this.main = new DiscussionThreadView({
                        el: DiscussionUtil.forumDiv,
                        model: this.thread,
                        mode: 'tab',
                        startHeader: this.startHeader,
                        courseSettings: this.courseSettings,
                        is_commentable_divided: this.discussion.is_commentable_divided
                    });
                    this.main.render();
                    this.discussionBoardView.discussionThreadListView.addRemoveTwoCol();
                    return this.thread.on('thread:thread_type_updated', this.showMain);
                },

                navigateToThread: function(threadId) {
                    var thread = this.discussion.get(threadId);
                    return this.navigate('' + (thread.get('commentable_id')) + '/threads/' + threadId, {
                        trigger: true
                    });
                },

                navigateToAllThreads: function() {
                    return this.navigate('', {
                        trigger: true
                    });
                },

                showNewPost: function() {
                    var self = this;
                    return DiscussionUtil.forumDiv.fadeOut({
                        duration: 200,
                        complete: function() {
                            $('aside.forum-nav').hide();
                            return self.newPostView.$el.fadeIn(200);
                        }
                    });
                },

                hideNewPost: function() {
                    var self = this;
                    return this.newPostView.$el.fadeOut({
                        duration: 200,
                        complete: function() {
                            $('aside.forum-nav').show();
                            if(!DiscussionUtil.emptyMessage.is(':visible')) {
                                DiscussionUtil.forumDiv.fadeIn(200).find('.thread-wrapper').focus();
                            }
                            return self.discussionBoardView.discussionThreadListView.addRemoveTwoCol();
                        }
                    });
                }

            });

            return DiscussionRouter;
        });
}).call(this, define || RequireJS.define);
