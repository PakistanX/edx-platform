/* globals DiscussionTopicMenuView, DiscussionUtil */
(function() {
    'use strict';
    if (Backbone) {
        this.DiscussionThreadEditView = Backbone.View.extend({
            tagName: 'form',
            events: {
                submit: 'updateHandler',
                'click .post-cancel': 'cancelHandler'
//                'click span.theme-opener': 'toggleThemeEditMobile'
            },

            attributes: {
                class: 'forum-new-post-form discussion-post edit-post-form'
            },

            initialize: function(options) {
                this.container = options.container || $('.thread-content-wrapper');
                this.mode = options.mode || 'inline';
                this.startHeader = options.startHeader;
                this.course_settings = options.course_settings;
                this.threadType = this.model.get('thread_type');
                this.topicId = this.model.get('commentable_id');
                this.context = options.context || 'course';
                _.bindAll(this, 'updateHandler', 'cancelHandler');
                return this;
            },

            render: function() {
                var formId = _.uniqueId('form-'),
                    threadTypeTemplate = edx.HtmlUtils.template($('#thread-type-template').html()),
                    $threadTypeSelector = $(threadTypeTemplate({form_id: formId}).toString()),
                    context,
                    mainTemplate = edx.HtmlUtils.template($('#thread-edit-template').html());
                context = $.extend({mode: this.mode, startHeader: this.startHeader}, this.model.attributes);
                edx.HtmlUtils.setHtml(this.$el, mainTemplate(context));
                this.container.append(this.$el);
                this.$submitBtn = this.$('.post-update');
                this.addField($threadTypeSelector);
                this.$('#' + formId + '-post-type-' + this.threadType).attr('checked', true);
                // Only allow the topic field for course threads, as standalone threads
                // cannot be moved.
                if (this.isTabMode()) {
                    this.topicView = new DiscussionTopicMenuView({
                        topicId: this.topicId,
                        course_settings: this.course_settings
                    });
                    this.addField(this.topicView.render());
                }
                DiscussionUtil.makeWmdEditor(this.$el, $.proxy(this.$, this), 'edit-post-body');
//                this.renderEditShowMoreBtn();
                return this;
            },

            addField: function($fieldView) {
                this.$('.forum-edit-post-form-wrapper').append($fieldView);
                return this;
            },

            isTabMode: function() {
                return this.mode === 'tab';
            },

            save: function() {
                var title = this.$('.edit-post-title').val(),
                    threadType = this.$('.input-radio:checked').val(),
                    body = this.$('.edit-post-body textarea').val(),
                    discussionIdChanged = false,
                    postData = {
                        title: title,
                        thread_type: threadType,
                        body: body
                    };
                if (this.topicView) {
                    postData.commentable_id = this.topicView.getCurrentTopicId();
                    if(this.topicId !== postData.commentable_id){
                        discussionIdChanged = true;
                    }
                }

                return DiscussionUtil.safeAjax({
                    $elem: this.$submitBtn,
                    $loading: this.$('button.post-cancel'),
                    url: DiscussionUtil.urlFor('update_thread', this.model.id),
                    type: 'POST',
                    dataType: 'json',
                    data: postData,
                    error: DiscussionUtil.formErrorHandler(this.$('.post-errors')),
                    success: function() {
                        this.$('.edit-post-title').val('').attr('prev-text', '');
                        this.$('.edit-post-body textarea').val('').attr('prev-text', '');
                        this.$('.wmd-preview p').html('');
                        if (this.topicView) {
                            postData.courseware_title = this.topicView.getFullTopicName();
                        }
                        this.model.set(postData).unset('abbreviatedBody');
                        this.trigger('thread:updated');
                        if (this.threadType !== threadType) {
                            this.model.set('thread_type', threadType);
                            this.model.trigger('thread:thread_type_updated');
                            this.trigger('comment:endorse');
                        }
                        if ($('a.back').is(':visible') && discussionIdChanged){
                            DiscussionUtil.forumDiv.hide();
                        }
                    }.bind(this)
                });
            },

            updateHandler: function(event) {
                event.preventDefault();
                // this event is for the moment triggered and used nowhere.
                this.trigger('thread:update', event);
                this.save();
                return this;
            },

            cancelHandler: function(event) {
                event.preventDefault();
                this.trigger('thread:cancel_edit', event);
                this.remove();
                return this;
            }

            renderEditShowMoreBtn = function(){
//              var span = this.$('span.theme-opener');
//              if (DiscussionUtil.themeCount > 2){
//                var text = span.find('span.text');
//                text.html('show more (' + DiscussionUtil.themeCount + ')');
//                text.attr('data-theme-count', DiscussionUtil.themeCount);
//              }
//              else {
//                span.hide();
//              }
            }

            toggleThemeEditMobile: function(event){
//                var themeOpener = this.$('span.theme-opener');
//                if (themeOpener.is(':visible')){
//                  var themeBoxes = $('div.theme-boxes'), spanText = themeOpener.find('span.text');
//                  if (themeBoxes.hasClass('show')){
//                    themeBoxes.removeClass('show');
//                    spanText.html('show more (' + spanText.attr('data-theme-count') + ')');
//                  }
//                  else {
//                    themeBoxes.addClass('show');
//                    spanText.html('show less');
//                  }
//                }
                return this;
            };
        });
    }
}).call(window);
