(function(define) {
    'use strict';
    define(['jquery', 'backbone', 'jquery.url'],
        function($, Backbone) {
            return Backbone.Model.extend({
                defaults: {
                    email: '',
                    name: '',
                    username: '',
                    password: '',
                    level_of_education: '',
                    gender: '',
                    year_of_birth: '',
                    mailing_address: '',
                    goals: ''
                },
                ajaxType: '',
                urlRoot: '',

                initialize: function(attributes, options) {
                    this.ajaxType = options.method;
                    this.urlRoot = options.url;
                },

                sync: function(method, model) {
                    var headers = {'X-CSRFToken': $.cookie('csrftoken')},
                        data = {},
                        courseId = $.url('?course_id');

                // If there is a course ID in the query string param,
                // send that to the server as well so it can be included
                // in analytics events.
                    if (courseId) {
                        data.course_id = decodeURIComponent(courseId);
                    }

                // Include all form fields and analytics info in the data sent to the server
                    $.extend(data, model.attributes);
                    const urlParams = new URLSearchParams(Backbone.history.location.search);
                    data.next = urlParams.get('next');
                    $.ajax({
                        url: model.urlRoot,
                        type: model.ajaxType,
                        data: data,
                        headers: headers,
                        success: function() {
                            try{
                              window.dataLayer = window.dataLayer || [];
                              window.dataLayer.push({
                                event: "sign_up",
                                method: "Direct"
                              });
                            } catch (error){
                              console.log('gtag failed');
                              console.log(error);
                            }
                            model.trigger('sync');
                        },
                        error: function(error) {
                            model.trigger('error', error);
                        }
                    });
                }
            });
        });
}).call(this, define || RequireJS.define);
